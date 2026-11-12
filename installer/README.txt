FastVideo Bridge installer

Ready package: ../dist/FastVideoBridge-Setup-0.0.2.34.exe
Default installation directory: C:\FastVideoBridge (selectable).
Python 3.12.10 x64, pinned Python dependencies, Go gateway and OMT DLLs are bundled.
The supplied signed NDI Tools installer is included as an optional setup task.
No development user records, credentials, source choices, captures or logs are packaged.

Build again:
  powershell -NoProfile -ExecutionPolicy Bypass -File Build-BridgeInstaller.ps1
Or pass -CompilerPath and -PythonExe explicitly. Inno Setup is needed only to build;
it is not needed on the target machine. Follow the compiler vendor's license terms.
An existing staging directory is never automatically removed or overwritten.

Build verification:
Hidden/System attributes are cleared only in the new staging directory.
The staged embedded Python must import all major runtime dependencies.
After compilation, the actual installer is installed into a unique temporary
folder without shortcuts, NDI Tools installation or production outputs.
Every installed payload file is compared with PAYLOAD-MANIFEST.json.
The installed Python, supervisor, server, gateway and UI are then checked.
Existing per-user Bridge registration is backed up and restored after the test.
The build fails if files, imports or startup checks fail. See smoke-report*.json.

Windows 10/11 x64; Current graphics drivers; default H264 selects NVENC / QSV / CPU automatically.
NDI Tools installation is optional; select it when NDI runtime is not already installed.
The installer creates Start and Stop shortcuts; normal operation stays hidden.
Settings, user records and logs are preserved on upgrades and uninstall.
No automatic startup task or router/firewall rule is added.

The development copy and C:\Fastvideo remain separate from the installed Bridge.
