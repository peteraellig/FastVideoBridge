param(
    [ValidateSet('Check','Apply','Remove')][string]$Action = 'Check',
    [string]$Program = ''
)
# Sets "Prefer maximum performance" in the NVIDIA driver profile of the Bridge
# runtime. A laptop GPU otherwise downclocks between frames and NVENC/decode
# take about twice as long.
$ErrorActionPreference = 'Stop'
if (-not $Program) { $Program = Join-Path (Split-Path $PSScriptRoot -Parent) '.runtime\python.exe' }
$Program = [System.IO.Path]::GetFullPath($Program)

$source = @'
using System;
using System.Runtime.InteropServices;

public static class BridgeNvProfile
{
    [DllImport("nvapi64.dll", EntryPoint = "nvapi_QueryInterface", CallingConvention = CallingConvention.Cdecl)]
    static extern IntPtr QueryInterface(uint id);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int NoArgs();
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int CreateSession(out IntPtr session);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int SessionOnly(IntPtr session);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int FindApp(IntPtr session, IntPtr name, out IntPtr profile, IntPtr app);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int FindProfile(IntPtr session, IntPtr name, out IntPtr profile);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int CreateProfile(IntPtr session, IntPtr info, out IntPtr profile);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int ProfilePtr(IntPtr session, IntPtr profile, IntPtr data);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int ProfileOnly(IntPtr session, IntPtr profile);
    [UnmanagedFunctionPointer(CallingConvention.Cdecl)] delegate int GetSetting(IntPtr session, IntPtr profile, uint id, IntPtr setting);

    // IDs from NVIDIA's public nvapi_interface.h
    const uint INITIALIZE = 0x0150E828, CREATE_SESSION = 0x0694D52E, DESTROY_SESSION = 0xDAD9CFF8,
               LOAD = 0x375DBD6B, SAVE = 0xFCBC7E14, CREATE_PROFILE = 0xCC176068, DELETE_PROFILE = 0x17093206,
               FIND_PROFILE = 0x7E4A9A0B, FIND_APP = 0xEEE566B2, CREATE_APP = 0x4347A9DE, DELETE_APP = 0x2C694BC6,
               SET_SETTING = 0x577DD202, GET_SETTING = 0x73BF8338, PROFILE_INFO = 0x61CD6FD6;
    const uint PSTATE_SETTING = 0x1057EB71;   // Power management mode
    const uint PREFER_MAX = 1;
    const int APP_SIZE = 4 + 4 + 4 * 4096 + 4, SETTING_SIZE = 12320, PROFILE_SIZE = 4116, NAME_SIZE = 4096;
    const int NOT_FOUND = -160, PROFILE_NOT_FOUND = -163, APP_NOT_FOUND = -166, NO_PRIVILEGE = -137;

    static T Fn<T>(uint id) where T : class
    {
        IntPtr p = QueryInterface(id);
        if (p == IntPtr.Zero) throw new Exception("NVAPI function 0x" + id.ToString("X8") + " is not available in this driver");
        return Marshal.GetDelegateForFunctionPointer(p, typeof(T)) as T;
    }

    static void Check(int rc, string what)
    {
        if (rc == NO_PRIVILEGE) throw new UnauthorizedAccessException(what + ": administrator rights required");
        if (rc != 0) throw new Exception(what + " failed (NVAPI " + rc + ")");
    }

    static IntPtr Zeroed(int size, int version)
    {
        IntPtr p = Marshal.AllocHGlobal(size);
        for (int i = 0; i < size; i++) Marshal.WriteByte(p, i, 0);
        if (version != 0) Marshal.WriteInt32(p, 0, size | (version << 16));
        return p;
    }

    static void PutWide(IntPtr p, int offset, string text)
    {
        byte[] b = System.Text.Encoding.Unicode.GetBytes(text);
        Marshal.Copy(b, 0, IntPtr.Add(p, offset), Math.Min(b.Length, NAME_SIZE - 2));
    }

    static IntPtr Session()
    {
        Check(Fn<NoArgs>(INITIALIZE)(), "NVAPI initialization");
        IntPtr s;
        Check(Fn<CreateSession>(CREATE_SESSION)(out s), "Opening driver settings");
        int rc = Fn<SessionOnly>(LOAD)(s);
        if (rc != 0) { Fn<SessionOnly>(DESTROY_SESSION)(s); Check(rc, "Loading driver settings"); }
        return s;
    }

    static IntPtr FindApplication(IntPtr s, string program)
    {
        IntPtr name = Zeroed(NAME_SIZE, 0), app = Zeroed(APP_SIZE, 3);
        try
        {
            PutWide(name, 0, program);
            IntPtr profile;
            int rc = Fn<FindApp>(FIND_APP)(s, name, out profile, app);
            if (rc == APP_NOT_FOUND || rc == PROFILE_NOT_FOUND) return IntPtr.Zero;
            Check(rc, "Looking up the program profile");
            return profile;
        }
        finally { Marshal.FreeHGlobal(name); Marshal.FreeHGlobal(app); }
    }

    static int ReadMode(IntPtr s, IntPtr profile, out bool own)
    {
        IntPtr setting = Zeroed(SETTING_SIZE, 1);
        try
        {
            own = false;
            int rc = Fn<GetSetting>(GET_SETTING)(s, profile, PSTATE_SETTING, setting);
            if (rc == NOT_FOUND) return -1;
            Check(rc, "Reading the power management mode");
            own = Marshal.ReadInt32(setting, 4108) == 0;
            return Marshal.ReadInt32(setting, 8220);
        }
        finally { Marshal.FreeHGlobal(setting); }
    }

    static string ProfileName(IntPtr s, IntPtr profile)
    {
        IntPtr info = Zeroed(PROFILE_SIZE, 1);
        try { return Fn<ProfilePtr>(PROFILE_INFO)(s, profile, info) == 0 ? Marshal.PtrToStringUni(IntPtr.Add(info, 4)) : ""; }
        finally { Marshal.FreeHGlobal(info); }
    }

    // "max" = own profile prefers maximum performance; "other:<n>" = another mode; "missing" = no profile.
    public static string Status(string program)
    {
        IntPtr s = Session();
        try
        {
            IntPtr profile = FindApplication(s, program);
            if (profile == IntPtr.Zero) return "missing";
            bool own;
            int mode = ReadMode(s, profile, out own);
            return own && mode == PREFER_MAX ? "max" : "other:" + mode;
        }
        finally { Fn<SessionOnly>(DESTROY_SESSION)(s); }
    }

    public static void Apply(string program)
    {
        string key = program.ToLowerInvariant();
        IntPtr s = Session();
        try
        {
            IntPtr profile = FindApplication(s, program);
            if (profile == IntPtr.Zero)
            {
                // Same layout as the NVIDIA Control Panel: one profile named after
                // the full path, holding exactly this program.
                IntPtr name = Zeroed(NAME_SIZE, 0);
                try
                {
                    PutWide(name, 0, key);
                    int rc = Fn<FindProfile>(FIND_PROFILE)(s, name, out profile);
                    if (rc == PROFILE_NOT_FOUND)
                    {
                        IntPtr info = Zeroed(PROFILE_SIZE, 1);
                        try { PutWide(info, 4, key); Check(Fn<CreateProfile>(CREATE_PROFILE)(s, info, out profile), "Creating the program profile"); }
                        finally { Marshal.FreeHGlobal(info); }
                    }
                    else Check(rc, "Looking up the profile");
                }
                finally { Marshal.FreeHGlobal(name); }
                IntPtr app = Zeroed(APP_SIZE, 3);
                try { PutWide(app, 8, key.Replace('\\', '/')); Check(Fn<ProfilePtr>(CREATE_APP)(s, profile, app), "Adding the program to its profile"); }
                finally { Marshal.FreeHGlobal(app); }
            }
            IntPtr setting = Zeroed(SETTING_SIZE, 1);
            try
            {
                Marshal.WriteInt32(setting, 4100, (int)PSTATE_SETTING);
                Marshal.WriteInt32(setting, 8220, (int)PREFER_MAX);
                Check(Fn<ProfilePtr>(SET_SETTING)(s, profile, setting), "Setting maximum performance");
            }
            finally { Marshal.FreeHGlobal(setting); }
            Check(Fn<SessionOnly>(SAVE)(s), "Saving driver settings");
        }
        finally { Fn<SessionOnly>(DESTROY_SESSION)(s); }
    }

    public static bool Remove(string program)
    {
        IntPtr s = Session();
        try
        {
            IntPtr profile = FindApplication(s, program);
            if (profile == IntPtr.Zero) return false;
            if (ProfileName(s, profile).Equals(program, StringComparison.OrdinalIgnoreCase))
                Check(Fn<ProfileOnly>(DELETE_PROFILE)(s, profile), "Deleting the program profile");
            else
            {
                IntPtr name = Zeroed(NAME_SIZE, 0);
                try { PutWide(name, 0, program.ToLowerInvariant().Replace('\\', '/')); Check(Fn<ProfilePtr>(DELETE_APP)(s, profile, name), "Removing the program"); }
                finally { Marshal.FreeHGlobal(name); }
            }
            Check(Fn<SessionOnly>(SAVE)(s), "Saving driver settings");
            return true;
        }
        finally { Fn<SessionOnly>(DESTROY_SESSION)(s); }
    }
}
'@

$gpus = @(Get-CimInstance Win32_VideoController)
$nvidia = $gpus | Where-Object { $_.AdapterCompatibility -match 'NVIDIA' } | Select-Object -First 1
$result = [ordered]@{ program = $Program; gpus = @($gpus | ForEach-Object { $_.Name }); nvidia = [bool]$nvidia
                      nvidia_profile = 'not applicable'; message = '' }
try {
    if ($nvidia) {
        Add-Type -TypeDefinition $source -Language CSharp
        if ($Action -eq 'Apply') {
            if ([BridgeNvProfile]::Status($Program) -ne 'max') { [BridgeNvProfile]::Apply($Program) }
        } elseif ($Action -eq 'Remove') {
            [BridgeNvProfile]::Remove($Program) | Out-Null
        }
        # Always report what a fresh read of the driver database returns.
        $result.nvidia_profile = [BridgeNvProfile]::Status($Program)
    } else {
        $result.message = 'No NVIDIA GPU found; nothing to configure.'
    }
} catch [System.UnauthorizedAccessException] {
    $result.message = $_.Exception.Message
    $exitCode = 2
} catch {
    $result.message = $_.Exception.InnerException.Message
    if (-not $result.message) { $result.message = $_.Exception.Message }
    $exitCode = 1
}
$result | ConvertTo-Json -Compress
if ($exitCode) { exit $exitCode }
