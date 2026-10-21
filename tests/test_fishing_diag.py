#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Tests du canal de lecture du diagnostic NATIF du corps (fishing_diag).

On verifie les trois canaux de sortie de l'addon :
  A. la bande de pixels (encode ASCII) : decodage exact, auto-detection de
     l'echelle, refus d'une image sans marqueur ;
  B. les SavedVariables (WTF) : extraction des champs du diagnostic ;
  C. la ligne de chat : detection de sa presence.

Aucun de ces tests n'invente de valeur "jeu" : ils verifient la LECTURE
d'un contenu reellement produit par l'addon.
"""
import os
import sys

import cv2
import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import fishing_diag as FD  # noqa: E402

PAYLOAD = ("ghost=true | mapID=1430 | playerMap=0.3120,0.4471 (coordonnees "
           "normalisees 0..1) | corpseMap=0.3183,0.4560 (coordonnees "
           "normalisees 0..1) | worldPos=1234.56,-678.90,12.34 (yards) | "
           "RetrieveCorpse=None err=ERREUR: Protected function")
STRIP_MAX = 60


def build_strip(payload=PAYLOAD, sq=14, x0=30, y0=20, width=1400, height=900):
    """Reproduit exactement la mise en page de l'addon (drawStrip)."""
    img = np.zeros((height, width, 3), np.uint8)
    cells = []

    def place(col, row, rgb):
        cells.append((x0 + col * sq, y0 + row * sq, rgb))

    row, col = 0, 0
    place(col, row, (160, 0, 200))
    col += 1
    for ch in payload:
        if col > STRIP_MAX:
            row += 1
            col = 0
            place(col, row, (160, 0, 200))
            col += 1
        v = ord(ch)
        place(col, row, (v, v, v))
        col += 1
    for x, y, rgb in cells:
        img[y:y + sq, x:x + sq] = rgb[::-1]
    return img


# --------------------------------------------------------------------------
# A. bande de pixels
# --------------------------------------------------------------------------
@pytest.mark.parametrize("sq", [10, 12, 14, 16, 20])
def test_strip_decoded_exactly(sq):
    img = build_strip(sq=sq)
    text, scale = FD.read_strip_payload(img)
    assert text is not None, "bande non decodee (sq=%d)" % sq
    assert scale == sq
    flat = text.replace(" ", "")
    for key in ("mapID=1430", "corpseMap=0.3183", "playerMap=0.3120",
                "worldPos=1234.56", "RetrieveCorpse="):
        assert key in flat, "cle manquante : %s" % key


def test_strip_absent_returns_none():
    img = np.zeros((400, 800, 3), np.uint8)
    text, scale = FD.read_strip_payload(img)
    assert text is None
    assert scale is None


def test_strip_with_garbage_is_refused():
    """Une image sans marqueur de sync ne doit pas produire de donnee."""
    img = np.random.RandomState(0).randint(0, 255, (400, 800, 3), np.uint8)
    text, _ = FD.read_strip_payload(img)
    assert text is None


# --------------------------------------------------------------------------
# B. SavedVariables (WTF)
# --------------------------------------------------------------------------
WTF_SAMPLE = """
CasinobabeDB = {
  ["corpseDiag"] = {
    ["alive"] = false,
    ["castResult"] = "nil",
    ["corpseMap"] = {
      ["x"] = 0.31832,
      ["y"] = 0.45602,
    },
    ["corpseMapErr"] = "nil",
    ["ghost"] = true,
    ["mapID"] = 1430,
    ["playerMap"] = {
      ["x"] = 0.31205,
      ["y"] = 0.44711,
    },
    ["retrieveCorpseExists"] = true,
    ["retrieveSpellID"] = 5026,
    ["retrieveSpellKnown"] = true,
    ["worldX"] = 1234.56,
    ["worldY"] = -678.9,
    ["worldZ"] = 12.34,
  },
}
"""


def test_wtf_payload_extracted(tmp_path):
    path = tmp_path / "Casinobabe.lua"
    path.write_text(WTF_SAMPLE, encoding="utf-8")
    out = FD.read_wtf_payload(str(path))
    assert out is not None
    assert out["mapID"] == "1430"
    assert out["ghost"] == "true"
    assert out["worldX"] == "1234.56"
    assert out["playerMap"] == (0.31205, 0.44711)
    assert out["corpseMap"] == (0.31832, 0.45602)
    assert out["retrieveSpellID"] == "5026"


def test_wtf_without_diagnostic_returns_none(tmp_path):
    path = tmp_path / "Casinobabe.lua"
    path.write_text('CasinobabeDB = { ["foo"] = 1 }\n', encoding="utf-8")
    assert FD.read_wtf_payload(str(path)) is None


def test_wtf_missing_file_returns_none(tmp_path):
    assert FD.read_wtf_payload(str(tmp_path / "absent.lua")) is None


# --------------------------------------------------------------------------
# C. chat
# --------------------------------------------------------------------------
def test_chat_line_detection():
    """Une ligne de chat de diagnostic se distingue du texte du monde."""
    img = np.zeros((600, 900, 3), np.uint8)
    img[:, :] = (40, 40, 40)
    # une "ligne" de texte : petits blocs clairs separes (comme du texte)
    for i in range(24):
        x = 20 + i * 34
        cv2.rectangle(img, (x, 400), (x + 22, 428), (170, 170, 170), -1)
    found, bbox = FD.read_chat_payload(img)
    assert bbox is not None, "aucune ligne detectee"


# --------------------------------------------------------------------------
# facade
# --------------------------------------------------------------------------
def test_collect_gathers_channels(tmp_path):
    img = build_strip(sq=14)
    path = tmp_path / "Casinobabe.lua"
    path.write_text(WTF_SAMPLE, encoding="utf-8")
    res = FD.collect(img=img, wtf_path=str(path))
    assert res["strip"] is not None
    assert res["wtf"] is not None
    assert FD.is_diagnostic_available(res) is True


def test_collect_without_diagnostic():
    res = FD.collect(img=np.zeros((100, 100, 3), np.uint8))
    assert res["strip"] is None
    assert res["wtf"] is None
    assert FD.is_diagnostic_available(res) is False
