param(
    [Parameter(Mandatory=$true)][string]$JsonPath,
    [Parameter(Mandatory=$true)][string]$SchemaPath
)
$valid = Test-Json -LiteralPath $JsonPath -SchemaFile $SchemaPath -ErrorAction SilentlyContinue
[bool]$valid | ConvertTo-Json -Compress
