@echo off
echo Installation des dependances...
pip install -r requirements.txt
echo.
echo Lancement du serveur Flask...
echo Ouvrez http://localhost:5000 dans votre navigateur
echo Admin: http://localhost:5000/admin (mot de passe: admin123)
echo.
python app.py
pause
