# Creates a desktop shortcut "Generative Fill for PhotoCraft" that opens the companion window.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$pyw = Join-Path $here '.venv\Scripts\pythonw.exe'
$desktop = [Environment]::GetFolderPath('Desktop')
$s = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop 'Generative Fill for PhotoCraft.lnk'))
$s.TargetPath = $pyw
$s.Arguments = '"' + (Join-Path $here 'GenFill.pyw') + '"'
$s.WorkingDirectory = $here
$s.IconLocation = 'C:\Program Files\PhotoCraft\photocraft.exe,0'
$s.Save()
Write-Output "Shortcut created on the desktop."
