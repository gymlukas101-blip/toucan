# Sign in to the Toucan Chrome profile

From the checkout, start the dedicated window with PowerShell.

~~~powershell
& "$env:LOCALAPPDATA\Toucan\venv\Scripts\python.exe" -m tools.browser.launch
~~~

Open **https://canvas.wisc.edu** in that window. Enter your NetID yourself and complete Duo. Verify that your Canvas dashboard appears. During initial setup, you can close the Toucan window once sign-in finishes. On a requested live dry run, leave the window open and tell the operator that sign-in is complete.

Chrome stores this session in `%LOCALAPPDATA%\Toucan\chrome-profile`. Toucan verifies that profile before attaching. Passwords and Duo prompts are owner-operated.
