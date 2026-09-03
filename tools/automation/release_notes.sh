#!/bin/bash
# PureVox - release-notes generator (single implementation path).
# Copyright (C) 2024-2026 a2heng <752848283@qq.com>
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Usage: bash tools/automation/release_notes.sh <tag> <outfile> <title-line...>
# Writes "<title-line>", a blank line, "**提交记录**", then the commit log
# from the previous tag to <tag> (or the last 15 commits for the first tag).
# Used by ci.yml (release) and lite.yml (release-lite).
set -euo pipefail
TAG="$1"; OUT="$2"; shift 2
prev="$(git describe --tags --abbrev=0 "${TAG}^" 2>/dev/null || true)"
{
    echo "$*"
    echo ""
    echo "**提交记录**"
    if [ -n "$prev" ]; then
        git log --pretty="format:- %h %s" "$prev..$TAG"
    else
        git log --pretty="format:- %h %s" -15
    fi
    echo ""
} > "$OUT"
