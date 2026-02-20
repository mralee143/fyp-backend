@echo off
REM Temporary script to regenerate Prisma client with Image model
REM This adds the Scripts folder to PATH for this session only

echo Adding Scripts folder to PATH...
set PATH=%PATH%;C:\Users\haider\AppData\Local\Packages\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\LocalCache\local-packages\Python313\Scripts

echo.
echo Regenerating Prisma client...
python -m prisma generate

echo.
echo Pushing schema to database...
python -m prisma db push

echo.
echo Done! You can now run the property-based tests.
pause
