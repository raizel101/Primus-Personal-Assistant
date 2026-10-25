$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $handle = [IntPtr]::new([Convert]::ToInt64([string]$request.window_id.Substring(2), 16))
    $window = [System.Windows.Automation.AutomationElement]::FromHandle($handle)
    if ($null -eq $window -or $window.Current.ProcessId -ne [int]$request.pid) {
        throw 'Target window changed or is unavailable'
    }
    $allowedTypes = @('Button', 'CheckBox', 'ComboBox', 'Edit', 'Hyperlink',
                      'ListItem', 'MenuItem', 'RadioButton', 'TabItem', 'TreeItem')
    $nodes = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                             [System.Windows.Automation.Condition]::TrueCondition)
    if ($request.mode -eq 'sensitive') {
        if ($nodes.Count -gt 2000) { throw 'Window has too many controls to inspect safely' }
        for ($index = 0; $index -lt $nodes.Count; $index++) {
            $current = $nodes.Item($index).Current
            if (-not $current.IsOffscreen -and ($current.IsPassword -or
                [string]$current.Name -match '(?i)password|passcode|one.time|two.factor|verification code|security code|\bPIN\b')) {
                [Console]::Out.WriteLine((@{ok=$true; sensitive=$true} | ConvertTo-Json -Compress))
                exit 0
            }
        }
        [Console]::Out.WriteLine((@{ok=$true; sensitive=$false} | ConvertTo-Json -Compress))
        exit 0
    }
    $matches = New-Object System.Collections.ArrayList
    $limit = [Math]::Min($nodes.Count, 2000)
    for ($index = 0; $index -lt $limit; $index++) {
        try {
            $node = $nodes.Item($index)
            $current = $node.Current
            if ($current.IsPassword) { continue }
            $kind = $current.ControlType.ProgrammaticName.Replace('ControlType.', '')
            if ($kind -notin $allowedTypes) { continue }
            $label = [string]$current.Name
            if ($label.Length -gt 120) { $label = $label.Substring(0, 120) }
            $runtimeId = ($node.GetRuntimeId() -join '.')
            $rect = $current.BoundingRectangle
            $pattern = $null
            $operation = $null
            if ($kind -eq 'CheckBox' -and
                $node.TryGetCurrentPattern([System.Windows.Automation.TogglePattern]::Pattern, [ref]$pattern)) {
                $operation = 'toggle'
            } elseif ($kind -in @('RadioButton', 'TabItem', 'ListItem', 'TreeItem') -and
                $node.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$pattern)) {
                $operation = 'select'
            } elseif ($kind -in @('Edit', 'ComboBox')) {
                $operation = 'focus'
            } elseif ($node.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) {
                $operation = 'invoke'
            }
            if ($null -eq $operation) { continue }
            $row = @{
                element_id = $runtimeId
                label = $label
                control_type = $kind
                operation = $operation
                enabled = [bool]$current.IsEnabled
                visible = -not [bool]$current.IsOffscreen
                bounds = @([int]$rect.X, [int]$rect.Y, [int]$rect.Width, [int]$rect.Height)
            }
            if ($request.mode -in @('activate', 'set_text')) {
                if ($runtimeId -ne [string]$request.element_id) { continue }
                $expected = $request.expected
                if ($label -ne [string]$expected.label -or $kind -ne [string]$expected.control_type -or
                    $operation -ne [string]$expected.operation -or
                    -not $row.enabled -or -not $row.visible) {
                    throw 'Target control changed or is unavailable'
                }
                if ($request.mode -eq 'set_text') {
                    if ($kind -ne 'Edit') { throw 'Target is not an editable field' }
                    $valuePattern = $null
                    if (-not $node.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,
                                                        [ref]$valuePattern) -or $valuePattern.Current.IsReadOnly) {
                        throw 'Target field does not support text replacement'
                    }
                    $valuePattern.SetValue([string]$request.text)
                } elseif ($operation -eq 'focus') {
                    $node.SetFocus()
                } elseif ($operation -eq 'toggle') {
                    $pattern.Toggle()
                } elseif ($operation -eq 'select') {
                    $pattern.Select()
                } elseif ($operation -eq 'invoke') {
                    $pattern.Invoke()
                }
                [Console]::Out.WriteLine((@{ok=$true; sent=$true; verified=$false} |
                                          ConvertTo-Json -Compress))
                exit 0
            }
            if ($request.mode -eq 'list' -and ($null -eq $request.element_id -or
                $runtimeId -eq [string]$request.element_id) -and ($null -eq $request.query -or
                $label.IndexOf([string]$request.query, [StringComparison]::OrdinalIgnoreCase) -ge 0)) {
                [void]$matches.Add($row)
                if ($matches.Count -ge 80) { break }
            }
        } catch [System.Windows.Automation.ElementNotAvailableException] {
            continue
        }
    }
    if ($request.mode -in @('activate', 'set_text')) { throw 'Target control disappeared' }
    if ($request.mode -ne 'list') { throw 'Unsupported accessibility operation' }
    [Console]::Out.WriteLine((@{ok=$true; controls=@($matches.ToArray());
                              truncated=($nodes.Count -gt $limit -or $matches.Count -ge 80)} |
                              ConvertTo-Json -Compress -Depth 5))
} catch {
    [Console]::Out.WriteLine((@{ok=$false; error=$_.Exception.Message} | ConvertTo-Json -Compress))
    exit 1
}
