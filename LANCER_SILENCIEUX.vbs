' Lance le bot sans faire apparaitre de fenetre noire toutes les 5 minutes.
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
dossier = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = dossier
shell.Run """" & dossier & "\LANCER_BOT_LOCAL.bat""", 0, False
