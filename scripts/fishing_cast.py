"""P0.35 : lancer la canne SANS dependre d'un raccourci (universel).

Niveau 3 de la hierarchie : si aucune touche ne produit de flotteur, le
bot passe par la commande chat
    /run CastSpellByName("Fishing")
qui ne depend d'aucun binding et marche sur n'importe quel personnage.

Ce module expose une primitive isolee (facilement testable) :
    cast_via_chat(hwnd, spell="Fishing") -> bool
Elle ouvre le chat (Entree), tape la commande, valide (Entree), puis
referme le chat (Echap).
"""
import ctypes
import sys
import time

u = ctypes.windll.user32
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
VK_RETURN, VK_ESCAPE = 0x0D, 0x1B


def _key(hwnd, vk, hold=0.05):
    u.SendMessageW(hwnd, WM_KEYDOWN, vk, 0x00000001 | (vk << 16) | (1 << 30))
    time.sleep(hold)
    u.SendMessageW(hwnd, WM_KEYUP, vk, 0x00000001 | (vk << 16) | (1 << 31))


def _char(hwnd, ch):
    """Tape un caractere (le client attend WM_CHAR pour le chat)."""
    u.PostMessageW(hwnd, 0x0102, ord(ch), 0)
    time.sleep(0.02)


def cast_via_chat(hwnd, spell="Fishing", settle=0.6):
    """Lance `spell` par la commande chat.  Retourne True si la commande
    a ete envoyee."""
    if not hwnd:
        return False
    cmd = '/run CastSpellByName("%s")' % spell
    try:
        _key(hwnd, VK_RETURN)                 # ouvre le chat
        time.sleep(settle)
        for ch in cmd:
            _char(hwnd, ch)
        time.sleep(0.15)
        _key(hwnd, VK_RETURN)                 # valide
        time.sleep(settle)
        _key(hwnd, VK_ESCAPE)                 # referme le chat
        return True
    except Exception:
        try:
            _key(hwnd, VK_ESCAPE)
        except Exception:
            pass
        return False


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    from fishing_observer import find_wow_window
    hwnd, _t, _r = find_wow_window()
    if not hwnd:
        print("  client absent")
        return 2
    ok = cast_via_chat(hwnd)
    print("  commande chat envoyee : %s" % ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
