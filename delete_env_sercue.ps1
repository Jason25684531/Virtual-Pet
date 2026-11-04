$target = 'D:\01_project\Virtual-Pet\VirtualPet_Release\config\.env.sercue'

if (Test-Path -LiteralPath $target -PathType Leaf) {
    Remove-Item -LiteralPath $target -Force -ErrorAction Stop
}
