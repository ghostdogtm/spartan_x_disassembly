@echo off
cd /d "%~dp0"
del game.nes game.map game_labels.txt 2>nul
ca65 -g src/main.asm -o src/main.o
if errorlevel 1 exit /b 1
ld65 -C config.cfg -Ln game_labels.txt -m game.map -o game.nes src/main.o
