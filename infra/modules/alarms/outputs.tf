output "alarm_names" {
  value = [
    aws_cloudwatch_metric_alarm.error_rate.alarm_name,
    aws_cloudwatch_metric_alarm.latency_p95.alarm_name,
    aws_cloudwatch_metric_alarm.lambda_errors.alarm_name,
    aws_cloudwatch_metric_alarm.lambda_throttles.alarm_name,
    aws_cloudwatch_metric_alarm.dynamodb_throttles.alarm_name,
  ]
}

output "alarm_metric_count" {
  description = "Alarm metrics used; the free tier allows 10 per account. Math alarms count each metric they read."
  value = 3 + sum([
    for alarm in [aws_cloudwatch_metric_alarm.error_rate, aws_cloudwatch_metric_alarm.dynamodb_throttles] :
    length([for query in alarm.metric_query : query.id if length(query.metric) > 0])
  ])
}
