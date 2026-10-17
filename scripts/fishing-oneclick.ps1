<#
.SYNOPSIS
  Casinobabe - session peche "1 bouton" : sync addon -> lance WoW -> lance le bot en
  arriere-plan -> attend la porte "perso en jeu".

.DESCRIPTION
  Ce script est la REPLAYabilite de la sequence manuelle (Battle.net -> WoW ->
  bot). Il est idempotent : chaque etape detecte l'etat reel avant d'agir.

  Etapes :
    1. Preflight   : python + dossier repo + WoW detecte.
    2. Sync addon  : runtime-addon\Casinobabe -> <WoWRoot>\Interface\AddOns\Casinobabe
                     (copie uniquement, JAMAIS de suppression : les backups live
                     et les fichiers absents du source restent intacts).
    3. WoW         : si la fenetre "World of Warcraft" existe on la restaure et on
                     la met au premier plan (obligatoire : le bot n'envoie aucune
                     touche si WoW n'a pas le focus). Sinon lancement via
                     Battle.net --launch=wow_classic_anniversary, attente de la
                     fenetre, restauration, premier plan.
    4. Bot         : Start-Process detourne (python.exe --cli, fenetre minimizee,
                     stdout/stderr dans logs\). Le bot demarre meme AVANT le login :
                     sa porte d'entree exige le HUD "perso en jeu" et n'envoie
                     aucune touche pendant l'ecran de login.
    5. Porte in-game: lecture de reports\fishing-bot-log.jsonl jusqu'a
                     KEYBINDING_CHECK (porte franchie) ou timeout.

  Le login Battle.net reste MANUEL (identifiants) : aucun script ne doit les saisir.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\fishing-oneclick.ps1

.EXAMPLE
  .\scripts\fishing-oneclick.ps1 -SkipWow -GateTimeoutSec 0
#>
[CmdletBinding()]
param(
  # Ne pas lancer WoW (deja ouvert).
  [switch]$SkipWow,
  # Ne pas lancer le bot (verifications / sync seules).
  [switch]$SkipBot,
  # Ne pas synchroniser l'addon vers le dossier live.
  [switch]$SkipSync,
  # Nombre de clients WoW a ouvrir (2 = 2 persos du meme compte bnet).
  [ValidateRange(1, 2)]
  [int]$Clients = 1,
  # Fenetre(s) WoW sur laquelle(s) demarrer un bot : "1", "2", "last"...
  # Defaut : la 1re fenetre. Exemple -BotInstances "1,2" = un bot par client.
  [string]$BotInstances = "1",
  # Windows n'accorde le focus clavier qu'a UNE fenetre : deux bots de peche
  # simultanes ne peuvent pas s'envoyer des touches. On refuse par defaut.
  [switch]$AllowFocusRace,
  # Delai max d'attente de la fenetre WoW (secondes).
  [int]$WowTimeoutSec = 240,
  # Delai max d'attente de la porte "perso en jeu" (0 = ne pas attendre).
  [int]$GateTimeoutSec = 300,
  # Ne pas attendre : rend la main juste apres le demarrage du bot.
  [switch]$NoWait
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$StateDir = Join-Path $Repo "state"
$LogsDir = Join-Path $Repo "logs"
$ReportsDir = Join-Path $Repo "reports"
New-Item -ItemType Directory -Force -Path $StateDir, $LogsDir, $ReportsDir | Out-Null

$BotScript = Join-Path $Repo "scripts\fishing_bot.py"
$BotLog = Join-Path $ReportsDir "fishing-bot-log.jsonl"
$BotPidFile = Join-Path $StateDir "fishing-bot.pid"
$SummaryFile = Join-Path $StateDir "oneclick-last-run.json"
$StartedAt = Get-Date
$Steps = [System.Collections.ArrayList]::new()

function Write-Step([string]$msg) {
  Write-Host ("[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $msg)
}
function Write-Ok([string]$msg) {
  Write-Host ("[{0}]   OK  {1}" -f (Get-Date -Format "HH:mm:ss"), $msg) -ForegroundColor Green
}
function Write-Ko([string]$msg) {
  Write-Host ("[{0}]   KO  {1}" -f (Get-Date -Format "HH:mm:ss"), $msg) -ForegroundColor Red
}
function Add-Step([string]$name, [string]$status, [string]$detail = "") {
  [void]$Steps.Add([ordered]@{ step = $name; status = $status; detail = $detail })
}

if (-not ("Casinobabe.Win32" -as [type])) {
  Add-Type -Namespace "Casinobabe" -Name "Win32" -MemberDefinition @"
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr FindWindowW(string lpClassName, string lpWindowName);
    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);
"@
}

function Get-WowWindows {
  # Fenetres WoW visibles, ordre d'EnumWindows : c'est cet ordre qui definit
  # 'first'/'second' cote bot (--instance).
  $found = New-Object System.Collections.ArrayList
  $probe = @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class CasinobabeWowEnum {
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowTextLengthW(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
  delegate bool EnumProc(IntPtr h, IntPtr l);
  public static List<string> Titles() {
    var res = new List<string>();
    EnumWindows(delegate(IntPtr h, IntPtr l) {
      uint pid; GetWindowThreadProcessId(h, out pid);
      if (pid != 0 && IsWindowVisible(h)) {
        int n = GetWindowTextLengthW(h);
        if (n > 0) {
          var sb = new StringBuilder(n + 1);
          GetWindowTextW(h, sb, n + 1);
          if (sb.ToString() == "World of Warcraft") res.Add(h.ToInt64().ToString());
        }
      }
      return true;
    }, IntPtr.Zero);
    return res;
  }
}
"@
  if (-not ("CasinobabeWowEnum" -as [type])) { Add-Type -TypeDefinition $probe -Language CSharp }
  foreach ($h in [CasinobabeWowEnum]::Titles()) { [void]$found.Add([IntPtr]$h) }
  return $found
}

function Get-WowWindow {
  return [Casinobabe.Win32]::FindWindowW($null, "World of Warcraft")
}

function Focus-WowWindow {
  <# Restaure + premier plan. Le bot n'envoie AUCUNE touche quand WoW n'a pas le
     focus (FOCUS_LOST -> INPUT_PAUSED) : cette etape n'est pas cosmetique. #>
  $hwnd = Get-WowWindow
  if ($hwnd -eq [IntPtr]::Zero) { return $false }
  if ([Casinobabe.Win32]::IsIconic($hwnd)) {
    [void][Casinobabe.Win32]::ShowWindow($hwnd, 9) # SW_RESTORE
    Start-Sleep -Milliseconds 400
  }
  [void][Casinobabe.Win32]::SetForegroundWindow($hwnd)
  Start-Sleep -Milliseconds 300
  return $true
}

function Get-PythonExe {
  foreach ($cand in @("python.exe", "pythonw.exe", "py.exe")) {
    $found = Get-Command $cand -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
  }
  return $null
}

function Find-WowRoot {
  # Priorite : le client ou le Casinobabe live existe deja (preuve d'usage).
  $bases = @(
    "C:\Program Files (x86)\World of Warcraft",
    "C:\Program Files\World of Warcraft"
  ) | Where-Object { Test-Path $_ }
  foreach ($b in $bases) {
    foreach ($v in @("_anniversary_", "_classic_", "_classic_era_", "_retail_")) {
      $exe = Join-Path (Join-Path $b $v) "WowClassic.exe"
      $exeR = Join-Path (Join-Path $b $v) "Wow.exe"
      if ((Test-Path $exe) -or (Test-Path $exeR)) {
        [pscustomobject]@{ Root = (Join-Path $b $v); Exe = $(if (Test-Path $exe) { $exe } else { $exeR }) }
      }
    }
  }
}

$summary = [ordered]@{
  schema     = 1
  startedAt  = $StartedAt.ToUniversalTime().ToString("o")
  finishedAt = $null
  ok         = $false
  repo       = $Repo
  wowRoot    = $null
  botPid     = $null
  gate       = "pending"
  steps      = $Steps
}

try {
  # ---------------------------------------------------------------- 1. preflight
  Write-Step "Preflight"
  if (-not (Test-Path $BotScript)) { throw "fishing_bot.py introuvable : $BotScript" }
  $python = Get-PythonExe
  if (-not $python) { throw "Python introuvable dans le PATH." }
  Write-Ok "python = $python"

  $wow = Find-WowRoot | Select-Object -First 1
  if (-not $wow) { throw "Aucun client WoW trouve (_anniversary_ / _classic_ / _retail_)." }
  $summary.wowRoot = $wow.Root
  Write-Ok "WoW = $($wow.Root)"

  # ---------------------------------------------------------------- 2. sync addon
  $liveAddon = Join-Path $wow.Root "Interface\AddOns\Casinobabe"
  $srcAddon = Join-Path $Repo "runtime-addon\Casinobabe"
  if ($SkipSync) {
    Write-Step "Sync addon : ignore (-SkipSync)"
    Add-Step "sync-addon" "skipped" ""
  } else {
    Write-Step "Sync addon -> live"
    if (-not (Test-Path $srcAddon)) {
      Write-Ko "source absent : $srcAddon"
      Add-Step "sync-addon" "failed" "source absent"
    } else {
      New-Item -ItemType Directory -Force -Path $liveAddon | Out-Null
      $copied = @()
      Get-ChildItem $srcAddon -File -Recurse | ForEach-Object {
        $rel = $_.FullName.Substring($srcAddon.Length).TrimStart("\")
        $dst = Join-Path $liveAddon $rel
        $dstDir = Split-Path -Parent $dst
        if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
        $same = $false
        if (Test-Path $dst) {
          $same = ((Get-FileHash $_.FullName).Hash -eq (Get-FileHash $dst).Hash)
        }
        if (-not $same) {
          # Copie atomique : on ecrit un .tmp puis on remplace = jamais de fichier
          # live a moitie ecrit si le process est tue au milieu.
          $tmp = "$dst.sync-tmp"
          Copy-Item $_.FullName $tmp -Force
          Move-Item $tmp $dst -Force
          $copied += $rel
        }
      }
      if ($copied.Count -eq 0) {
        Write-Ok "addon deja a jour (rien a copier)"
        Add-Step "sync-addon" "ok" "aucun changement"
      } else {
        Write-Ok ("addon synchronise : {0} fichier(s) -> {1}" -f $copied.Count, ($copied -join ", "))
        Add-Step "sync-addon" "ok" ($copied -join ", ")
      }
      # Verification post-copie : les fichiers critiques doivent exister.
      foreach ($crit in @("Casinobabe.lua", "Casinobabe.toc", "CasinobabeFishing.lua")) {
        if (-not (Test-Path (Join-Path $liveAddon $crit))) { throw "Fichier critique absent apres sync : $crit" }
      }
    }
  }

  # ---------------------------------------------------------------- 3. WoW
  if ($SkipWow) {
    Write-Step "WoW : ignore (-SkipWow)"
    Add-Step "wow" "skipped" ""
  } elseif ((Get-WowWindow) -ne [IntPtr]::Zero) {
    Write-Step "WoW deja ouvert : restauration + premier plan"
    [void](Focus-WowWindow)
    Write-Ok "fenetre WoW au premier plan"
    Add-Step "wow" "ok" "deja ouvert"
  } else {
    Write-Step "Lancement WoW via Battle.net"
    $bnet = "C:\Program Files (x86)\Battle.net\Battle.net.exe"

    # --- CAUSE RACINE du crash "Blizzard Error" du 2026-10-17 -------------
    # Le script NE DOIT PAS lancer une 2e instance de Battle.net : deux
    # clients bnet concurrents = "L'application a rencontré une erreur
    # inattendue" + aucun lancement de WoW. Pire, envoyer --launch à un
    # client qui vient d'être démarré (pas encore prêt à recevoir la
    # commande) fait NAISSRE une 2e instance au lieu de router vers la 1re.
    # Règle : 0 instance si absente -> 1 seule, on attend la fenêtre, puis
    # --launch UNE fois, et seulement si une fenêtre bnet AUTHENTIFIÉE
    # ("Battle.net") existe. Une fenêtre "Battle.net Login" = session
    # déconnectée : on s'arrête et l'utilisateur se connecte lui-même
    # (le script ne saisit JAMAIS d'identifiants).
    $bnetWins = @(Get-Process -Name "Battle.net" -ErrorAction SilentlyContinue |
                   Where-Object { $_.MainWindowTitle })
    if ($bnetWins.Count -eq 0) {
      if (-not (Test-Path $bnet)) {
        throw "Battle.net absent ($bnet) : lance la session bnet toi-meme puis relance."
      }
      Start-Process -FilePath $bnet | Out-Null
      Write-Step "Battle.net absent : demarrage de l'unique instance..."
      $t = (Get-Date).AddSeconds(90)
      while ((Get-Date) -lt $t) {
        $bnetWins = @(Get-Process -Name "Battle.net" -ErrorAction SilentlyContinue |
                       Where-Object { $_.MainWindowTitle })
        if ($bnetWins.Count -gt 0) { break }
        Start-Sleep -Seconds 2
      }
      if ($bnetWins.Count -eq 0) {
        throw "Battle.net demarre mais aucune fenetre en 90 s."
      }
      # Le client a besoin de finir son handshake avant d'accepter --launch.
      Start-Sleep -Seconds 10
      Add-Step "bnet" "ok" "instance demarree par le script"
    }

    $bnetWins = @(Get-Process -Name "Battle.net" -ErrorAction SilentlyContinue |
                   Where-Object { $_.MainWindowTitle })
    $titles = ($bnetWins | ForEach-Object { "'" + $_.MainWindowTitle + "'" }) -join " + "
    Write-Step "fenetres Battle.net : $titles"

    $loginWins = @($bnetWins | Where-Object { $_.MainWindowTitle -like "*Login*" })
    if ($loginWins.Count -gt 0) {
      throw "Battle.net n'est PAS connecte (fenetre 'Battle.net Login'). Connecte-toi sur Battle.net (identifiants saisis par toi uniquement), puis relance ce script : le bot, lui, peut demarrer avant et attendra le login."
    }
    # ATTENTION : le titre "Battle.net" apparait des la TENTATIVE de login
    # (fenetre de depart + Windows Hello), pas quand la session aboutit. Le
    # 2026-10-17 : apres "Use passkey" le titre valait "Battle.net", le
    # script a cru etre authentifie et a envoye --launch -> une 2e instance
    # s'est ouverte sur une fenetre "Battle.net Login" (puis crash Blizzard
    # Error au 2e essai). Le seul signal fiable reste l'absence de toute
    # fenetre Login ET l'apparition reelle du client WoW : on verifie donc
    # aussi, apres le --launch, qu'aucune nouvelle fenetre Login n'apparait.
    $authWins = @($bnetWins | Where-Object { $_.MainWindowTitle -eq "Battle.net" })
    if ($authWins.Count -eq 0) {
      throw "Aucune fenetre Battle.net authentifiee (titres vus : $titles). Reconnecte-toi puis relance."
    }
    if ($authWins.Count -gt 1) {
      throw ("{0} fenetres Battle.net authentifiees en meme temps ({1}) : deux clients bnet = crash 'Blizzard Error'. Ferme-en un, puis relance." -f $authWins.Count, $titles)
    }

    # product uid = wow_classic_anniversary (relve dans l'Agent Battle.net :
    # /game/wow_classic_anniversary -> _anniversary_/WoWClassic.exe)
    $already = @(Get-WowWindows).Count
    $toLaunch = [Math]::Max(0, $Clients - $already)
    for ($c = 0; $c -lt $toLaunch; $c++) {
      Start-Process -FilePath $bnet -ArgumentList "--launch=wow_classic_anniversary" | Out-Null
      Write-Step ("Battle.net --launch=wow_classic_anniversary envoye (client {0}/{1})" -f ($c + 1), $Clients)
      # Un 2e client doit etre demande APRES que le 1er a sa fenetre, sinon
      # bnet route la commande vers la meme instance et on n'ouvre qu'un jeu.
      $target = $already + $c + 1
      $d = (Get-Date).AddSeconds($WowTimeoutSec)
      while ((Get-Date) -lt $d) {
        if (@(Get-WowWindows).Count -ge $target) { break }
        Start-Sleep -Seconds 2
      }
      if (@(Get-WowWindows).Count -lt $target) {
        # Verifie si l'echec vient de l'auth : une fenetre Login apparue
        # apres le --launch = la session n'etait pas aboutie.
        $post = @(Get-Process -Name "Battle.net" -ErrorAction SilentlyContinue |
                  Where-Object { $_.MainWindowTitle -like "*Login*" })
        if ($post.Count -gt 0) {
          throw "L'authentification Battle.net n'a pas abouti (fenetre 'Battle.net Login' reapparue apres --launch). Termine la connexion (passkey/Windows Hello ou code email) PUIS relance : ne relance pas pendant que la fenetre Windows Hello est ouverte."
        }
        throw ("Fenetre 'World of Warcraft' {0}/{1} absente apres {2} s." -f $target, $Clients, $WowTimeoutSec)
      }
    }

    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
      if (@(Get-WowWindows).Count -ge $Clients) { break }
      Start-Sleep -Seconds 2
    }
    $hwnd = Get-WowWindow
    if ($hwnd -eq [IntPtr]::Zero) {
      throw "Fenetre 'World of Warcraft' absente apres $WowTimeoutSec s (le client met plus de temps a demarrer ou demande une mise a jour)."
    }
    Write-Ok ("fenetre(s) WoW detectee(s) : {0}" -f @(Get-WowWindows).Count)
    # Deux clients cote a cote : sinon le 2e est masque sous le 1re et le bot
    # ciblant le 2e peut-etre "capture ok" mais la fenetre reste derriere.
    $wins = @(Get-WowWindows)
    if ($wins.Count -ge 2) {
      if (-not ("System.Windows.Forms" -as [type])) { Add-Type -AssemblyName System.Windows.Forms }
      $screenW = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds.Width
      $half = [int]($screenW / $wins.Count)
      for ($i = 0; $i -lt $wins.Count; $i++) {
        [void][Casinobabe.Win32]::MoveWindow($wins[$i], ($i * $half), 0, $half, 1000, $true)
      }
      Write-Ok ("clients positionnes cote a cote ({0} x {1} px)" -f $half, 1000)
    }
    # Le client prend encore plusieurs secondes a rendre la fenetre : on retente
    # la mise au premier plan quelques fois avant de conclure.
    for ($i = 0; $i -lt 15; $i++) {
      if (Focus-WowWindow) { break }
      Start-Sleep -Seconds 2
    }
    Write-Ok "fenetre WoW restauree + premier plan"
    Add-Step "wow" "ok" "lance via Battle.net"
  }

  # ---------------------------------------------------------------- 4. bot
  if ($SkipBot) {
    Write-Step "Bot : ignore (-SkipBot)"
    Add-Step "bot" "skipped" ""
  } else {
    # Un bot par fenetre demandee. Chaque instance a son PID et son journal
    # (--instance N) : sinon le verrou anti-doublon refuse le 2e bot.
    $instList = @($BotInstances -split "," | ForEach-Object { $_.Trim().ToLower() } |
                  Where-Object { $_ }) 
    if ($instList.Count -eq 0) { $instList = @("1") }
    if ($instList.Count -gt 1 -and -not $AllowFocusRace) {
      throw ("{0} bots demandes ({1}) mais Windows ne donne le focus clavier qu'a UNE fenetre : le second resterait en FOCUS_LOST en boucle (send_key() refuse d'envoyer si le client cible n'est pas au premier plan, fishing_bot.py:203). Un seul client actif a la fois : corrige par un arbitre de focus, pas par un deuxieme bot. Relance avec -BotInstances '{2}', ou -AllowFocusRace si tu acceptes le blocage." -f $instList.Count, ($instList -join ","), $instList[0])
    }

    $logStart = 0
    if (Test-Path $BotLog) { $logStart = (Get-Item $BotLog).Length }
    $botProcs = @()
    foreach ($inst in $instList) {
      $isDefault = ($inst -eq "1")
      $pidFile = if ($isDefault) { $BotPidFile } else { Join-Path $StateDir "fishing-bot-$inst.pid" }
      if (Test-Path $pidFile) {
        $running = $false
        try { $running = $null -ne (Get-Process -Id ([int](Get-Content $pidFile -Raw).Trim()) -ErrorAction SilentlyContinue) } catch { $running = $false }
        if ($running) {
          Write-Ok "bot instance '$inst' deja en cours (PID $((Get-Content $pidFile -Raw).Trim()))"
          Add-Step "bot-$inst" "ok" "deja en cours"
          continue
        }
        # Residu de PID (crash / kill) : le verrou anti-doublon le refuserait.
        Remove-Item $pidFile -Force
        Write-Step "residu de PID supprime ($inst) : $pidFile"
      }
      $out = Join-Path $LogsDir "fishing-bot-cli.out.log"
      $err = Join-Path $LogsDir "fishing-bot-cli.err.log"
      $argList = @("`"$BotScript`"", "--cli")
      if (-not $isDefault) { $argList += @("--instance", $inst) }
      Write-Step "Demarrage du bot en arriere-plan (--cli $(if ($isDefault) { '' } else { "--instance $inst" }))"
      $proc = Start-Process -FilePath $python `
        -ArgumentList $argList `
        -WorkingDirectory $Repo `
        -WindowStyle Minimized `
        -RedirectStandardOutput $out `
        -RedirectStandardError $err `
        -PassThru
      Write-Ok "bot '$inst' lance : PID $($proc.Id)  (console: logs\fishing-bot-cli.out.log)"
      $summary.botPid = $proc.Id
      Add-Step "bot-$inst" "ok" "PID $($proc.Id)"
      if ($proc.WaitForExit(8000)) {
        throw "Le bot '$inst' s'est arrete immediatement (code $($proc.ExitCode)). Voir $err"
      }
      $botProcs += $proc
    }
    $proc = $botProcs[0]

    # ------------------------------------------------------------ 5. porte in-game
    if ($GateTimeoutSec -gt 0 -and -not $NoWait) {
      Write-Step "Attente de la porte 'perso en jeu' (max $GateTimeoutSec s)"
      Write-Host "        -> logine ton perso sur WoW si besoin, le bot n'envoie AUCUNE touche avant le HUD." -ForegroundColor DarkGray
      $gateDeadline = (Get-Date).AddSeconds($GateTimeoutSec)
      $gate = "pending"
      while ((Get-Date) -lt $gateDeadline) {
        if (Test-Path $BotLog) {
          $fs = [System.IO.File]::Open($BotLog, "Open", "Read", "ReadWrite")
          try {
            $null = $fs.Seek($logStart, "Begin")
            $reader = New-Object System.IO.StreamReader($fs)
            $new = $reader.ReadToEnd()
          } finally { if ($reader) { $reader.Dispose() } else { $fs.Dispose() } }
          if ($new -match '"event":\s*"KEYBINDING_CHECK"') { $gate = "ingame"; break }
          if ($new -match '"event":\s*"(BOT_FAIL|ERROR|FAIL)"') { $gate = "failed"; break }
        }
        $alive = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if (-not $alive) { $gate = "died"; break }
        Start-Sleep -Seconds 3
      }
      $summary.gate = $gate
      switch ($gate) {
        "ingame" { Write-Ok "porte franchie : HUD 'perso en jeu' detecte, le bot peche." ; Add-Step "gate" "ok" "ingame" }
        "failed" { Write-Ko "le bot a signale un echec (voir reports\fishing-bot-log.jsonl)" ; Add-Step "gate" "failed" "BOT_FAIL" }
        "died"   { Write-Ko "le process du bot est mort (voir $err)" ; Add-Step "gate" "failed" "process mort" }
        default  { Write-Ko "timeout : le perso n'est pas encore en jeu (lancer reste en attente)" ; Add-Step "gate" "timeout" "" }
      }
    } elseif ($NoWait) {
      $summary.gate = "skipped"
      Write-Step "-NoWait : retour immediat, le bot attend le login tout seul."
    }
  }

  $summary.ok = $true
}
catch {
  Write-Ko $_.Exception.Message
  $summary.ok = $false
  $summary.error = $_.Exception.Message
}
finally {
  $summary.finishedAt = (Get-Date).ToUniversalTime().ToString("o")
  $summary | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 $SummaryFile
  Write-Host ""
  Write-Host ("Resume -> {0}" -f $SummaryFile) -ForegroundColor Cyan
  if ($summary.ok) {
    Write-Host "Arret d'urgence : F9 (fonctionne meme bot cache). Arret complet : Stop-Process -Id $($summary.botPid)" -ForegroundColor Cyan
  }
}

if (-not $summary.ok) { exit 1 }
exit 0