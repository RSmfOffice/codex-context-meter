param(
    [int]$IntervalMs = 1200,
    [string]$RefreshEventName = '',
    [int]$ParentPid = 0
)

$ErrorActionPreference = 'Stop'
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
} catch {}

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$refreshEvent = $null
if ($RefreshEventName) {
    try {
        $refreshEvent = [Threading.EventWaitHandle]::OpenExisting($RefreshEventName)
    }
    catch {
        $refreshEvent = $null
    }
}

$buttonCondition = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::Button
)

function Write-State {
    param(
        [bool]$Ok,
        [string]$Title = '',
        [string]$ErrorText = ''
    )

    $payload = [ordered]@{
        ok = $Ok
        title = $Title
        error = $ErrorText
        timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    }
    [Console]::Out.WriteLine(($payload | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
}

$nextParentCheck = [DateTime]::MinValue
while ($true) {
    if ($ParentPid -gt 0 -and [DateTime]::UtcNow -ge $nextParentCheck) {
        if ($null -eq (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) {
            break
        }
        $nextParentCheck = [DateTime]::UtcNow.AddSeconds(2)
    }
    try {
        $process = Get-Process ChatGPT -ErrorAction SilentlyContinue |
            Where-Object { $_.MainWindowHandle -ne 0 } |
            Select-Object -First 1
        if ($null -eq $process) {
            Write-State -Ok $false -ErrorText 'codex-window-not-found'
        }
        else {
            $root = [System.Windows.Automation.AutomationElement]::FromHandle(
                [IntPtr]$process.MainWindowHandle
            )
            $currentTitle = ''
            $toolbarCondition = New-Object System.Windows.Automation.PropertyCondition(
                [System.Windows.Automation.AutomationElement]::NameProperty,
                'Chat toolbar'
            )
            $toolbar = $root.FindFirst(
                [System.Windows.Automation.TreeScope]::Descendants,
                $toolbarCondition
            )
            if ($null -ne $toolbar) {
                $textCondition = New-Object System.Windows.Automation.PropertyCondition(
                    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
                    [System.Windows.Automation.ControlType]::Text
                )
                $toolbarTexts = $toolbar.FindAll(
                    [System.Windows.Automation.TreeScope]::Descendants,
                    $textCondition
                )
                for ($index = 0; $index -lt $toolbarTexts.Count; $index++) {
                    $candidate = $toolbarTexts.Item($index).Current.Name
                    if ($candidate -and $candidate -notmatch '^Project:') {
                        $currentTitle = $candidate
                        break
                    }
                }
            }
            $selectedTitles = @()
            if (-not $currentTitle) {
                $buttons = $root.FindAll(
                    [System.Windows.Automation.TreeScope]::Descendants,
                    $buttonCondition
                )
                for ($index = 0; $index -lt $buttons.Count; $index++) {
                    $element = $buttons.Item($index)
                    $className = $element.Current.ClassName
                    if ($className -notmatch 'sidebar-item') {
                        continue
                    }
                    $tokens = $className -split '\s+'
                    if ($tokens -contains 'bg-primary-ghost-hover') {
                        $selectedTitles += $element.Current.Name
                    }
                }
            }
            $selectedTitles = @($selectedTitles | Where-Object { $_ } | Select-Object -Unique)
            if ($currentTitle) {
                $state = [ordered]@{ ok = $true; title = $currentTitle; error = '' }
            }
            elseif ($selectedTitles.Count -eq 1) {
                $state = [ordered]@{ ok = $true; title = $selectedTitles[0]; error = '' }
            }
            elseif ($selectedTitles.Count -eq 0) {
                $state = [ordered]@{ ok = $false; title = ''; error = 'selected-thread-not-found' }
            }
            else {
                $state = [ordered]@{
                    ok = $false
                    title = ($selectedTitles -join "`n")
                    error = 'multiple-selected-threads'
                }
            }
            Write-State -Ok ([bool]$state.ok) -Title ([string]$state.title) -ErrorText ([string]$state.error)
        }
    }
    catch {
        Write-State -Ok $false -ErrorText 'uiautomation-failed'
    }
    if ($null -ne $refreshEvent) {
        [void]$refreshEvent.WaitOne($IntervalMs)
    }
    else {
        Start-Sleep -Milliseconds $IntervalMs
    }
}
