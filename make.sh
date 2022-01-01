#!/bin/sh

# Tip: add argument `run` to directly run after build for fast testing

echo 'Creating Python Wheel package via Poetry'
poetry build -f wheel

echo 'Building to self-contained folder/app via PyInstaller'
poetry run python pyinstaller.py

if [ "$1" = 'run' ]; then
    shift
    ./dist/vinetrimmer/vinetrimmer "$@"
    exit
fi

echo 'Done! See /dist for output files.'
