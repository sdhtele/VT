# Tip: add argument `run` to directly run after build for fast testing

Write-Output 'Creating Python Wheel package via Poetry'
& 'poetry' build -f wheel

Write-Output 'Building to self-contained folder/app via PyInstaller'
& 'poetry' run python pyinstaller.py

if ($args[0] -eq 'run') {
    & 'dist/vinetrimmer/vinetrimmer.exe' ($args | Select-Object -Skip 1)
    exit
}

Write-Output 'Creating Windows installer via Inno Setup'
& 'iscc' setup.iss

Write-Output 'Done! See /dist for output files.'
