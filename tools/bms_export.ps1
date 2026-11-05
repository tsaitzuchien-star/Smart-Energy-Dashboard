# BMS DataLogger daily export -> Google Drive folder "BMS..." (read-only SELECT)
# Exports the last 3 full days + today-so-far of DataLogger.dbo.Table03 (all KmTagx columns),
# one CSV per day, overwriting, so a missed run is filled in automatically.
$ErrorActionPreference = 'Stop'
$Server  = 'localhost'
$LogDir  = 'C:\BMS_Export'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$Log = Join-Path $LogDir 'export.log'
function Write-Log($m) { Add-Content -Path $Log -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) }

try {
    # Google Drive for desktop shows up as its own drive (usually G:) with "My Drive" inside.
    # Look one level below each non-C drive root for a folder whose name starts with "BMS".
    $Out = $null
    $drives = Get-PSDrive -PSProvider FileSystem | Where-Object { $_.Name -ne 'C' } |
               Sort-Object { if ($_.Description -like '*Google*') { 0 } else { 1 } }
    foreach ($drv in $drives) {
        $hit = Get-ChildItem -Path $drv.Root -Directory -ErrorAction SilentlyContinue |
               ForEach-Object { Get-ChildItem -Path $_.FullName -Directory -Filter 'BMS*' -ErrorAction SilentlyContinue } |
               Select-Object -First 1
        if ($hit) { $Out = $hit.FullName; break }
    }
    if (-not $Out) { throw 'Google Drive folder BMS* not found' }

    $conn = New-Object System.Data.SqlClient.SqlConnection("Server=$Server;Database=DataLogger;Integrated Security=True;Connect Timeout=30")
    $conn.Open()
    for ($i = 3; $i -ge 0; $i--) {
        $day  = (Get-Date).Date.AddDays(-$i)
        $next = $day.AddDays(1).AddMinutes(1)   # include the 00:00 reading of the next day
        $cmd = $conn.CreateCommand()
        $cmd.CommandText = "SELECT * FROM dbo.Table03 WHERE CONVERT(datetime, DateTime) >= @a AND CONVERT(datetime, DateTime) < @b ORDER BY CONVERT(datetime, DateTime)"
        [void]$cmd.Parameters.AddWithValue('@a', $day)
        [void]$cmd.Parameters.AddWithValue('@b', $next)
        $da = New-Object System.Data.SqlClient.SqlDataAdapter($cmd)
        $dt = New-Object System.Data.DataTable
        [void]$da.Fill($dt)
        $file = Join-Path $Out ('Table03_{0}.csv' -f $day.ToString('yyyy-MM-dd'))
        $inv  = [System.Globalization.CultureInfo]::InvariantCulture
        $sb   = New-Object System.Text.StringBuilder
        [void]$sb.AppendLine((($dt.Columns | ForEach-Object { $_.ColumnName }) -join ','))
        foreach ($row in $dt.Rows) {
            $vals = foreach ($v in $row.ItemArray) {
                if ($v -is [DateTime]) { $v.ToString('yyyy/MM/dd HH:mm:ss', $inv) }
                elseif ($v -is [IFormattable]) { $v.ToString($null, $inv) }
                else { [string]$v }
            }
            [void]$sb.AppendLine(($vals -join ','))
        }
        $tmp = Join-Path $LogDir 'tmp.csv'
        [System.IO.File]::WriteAllText($tmp, $sb.ToString(), (New-Object System.Text.UTF8Encoding($false)))
        Copy-Item $tmp $file -Force
        Write-Log ("{0}: {1} rows -> {2}" -f $day.ToString('yyyy-MM-dd'), $dt.Rows.Count, $file)
    }
    $conn.Close()
} catch {
    Write-Log ("ERROR: " + $_.Exception.Message)
    exit 1
}
