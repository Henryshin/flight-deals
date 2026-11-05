# Threads 웹 에이전트를 로그온 시 자동 실행으로 등록하고 지금 켠다.
# 해제: Unregister-ScheduledTask -TaskName AfterworkThreadsAgent -Confirm:$false
$pyw = (Get-Command pythonw).Source
$act = New-ScheduledTaskAction -Execute $pyw `
  -Argument '"C:\side_PJT\Reels\threads-agent\threads_web_agent.py"' `
  -WorkingDirectory 'C:\side_PJT\Reels\threads-agent'
$trg = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName 'AfterworkThreadsAgent' -Action $act -Trigger $trg -Settings $set `
  -Description 'Telegram -> Threads web draft agent' -Force | Out-Null
Start-ScheduledTask -TaskName 'AfterworkThreadsAgent'
Get-ScheduledTask -TaskName 'AfterworkThreadsAgent' | Select-Object TaskName, State
