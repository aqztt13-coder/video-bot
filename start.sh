#!/bin/bash
python -m http.server ${PORT:-10000} &
python bot.py