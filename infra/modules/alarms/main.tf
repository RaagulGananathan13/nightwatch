# The five ShopLite alarms from spec section 8. NightWatch hears about them through EventBridge
# (every alarm state change lands on the default bus for free), so no SNS topics are needed.
#
# Free tier: 10 alarm metrics per account. A metric-math alarm counts one per metric it reads,
# so these five alarms use 7 (see the alarm_metric_count output).

locals {
  tags = merge(var.tags, {
    "nightwatch:service" = var.service
  })
  lambda_dims = { FunctionName = var.function_name }
}

# Failed requests as a share of all requests. The numerator is Lambda's Errors metric, not the
# function URL's Url5xxCount: when the function crashes, the URL answers HTTP 502, but measured on
# 2026-10-28, Url5xxCount stayed empty while Errors counted every failure (ADR 0001).
resource "aws_cloudwatch_metric_alarm" "error_rate" {
  alarm_name          = "${var.service}-5xx-rate"
  alarm_description   = "More than ${var.error_rate_percent}% of ${var.service} requests failed (HTTP 5xx) in a minute."
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.error_rate_percent
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  treat_missing_data  = "notBreaching"
  tags                = merge(local.tags, { "nightwatch:severity" = "high" })

  metric_query {
    id          = "rate"
    expression  = "100 * FILL(errors, 0) / requests"
    label       = "Failed requests (%)"
    return_data = true
  }

  metric_query {
    id = "errors"

    metric {
      namespace   = "AWS/Lambda"
      metric_name = "Errors"
      dimensions  = local.lambda_dims
      period      = 60
      stat        = "Sum"
    }
  }

  metric_query {
    id = "requests"

    metric {
      namespace   = "AWS/Lambda"
      metric_name = "UrlRequestCount"
      dimensions  = local.lambda_dims
      period      = 60
      stat        = "Sum"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "latency_p95" {
  alarm_name          = "${var.service}-latency-p95"
  alarm_description   = "${var.service} p95 latency stayed above ${var.latency_p95_ms} ms for 2 minutes."
  namespace           = "AWS/Lambda"
  metric_name         = "UrlRequestLatency"
  dimensions          = local.lambda_dims
  extended_statistic  = "p95"
  period              = 60
  comparison_operator = "GreaterThanThreshold"
  threshold           = var.latency_p95_ms
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  treat_missing_data  = "notBreaching"
  tags                = merge(local.tags, { "nightwatch:severity" = "medium" })
}

resource "aws_cloudwatch_metric_alarm" "lambda_errors" {
  alarm_name          = "${var.service}-lambda-errors"
  alarm_description   = "${var.service} Lambda failed ${var.lambda_error_threshold} or more times in a minute."
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = local.lambda_dims
  statistic           = "Sum"
  period              = 60
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = var.lambda_error_threshold
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  treat_missing_data  = "notBreaching"
  tags                = merge(local.tags, { "nightwatch:severity" = "high" })
}

resource "aws_cloudwatch_metric_alarm" "lambda_throttles" {
  alarm_name          = "${var.service}-lambda-throttles"
  alarm_description   = "${var.service} Lambda invocations were throttled."
  namespace           = "AWS/Lambda"
  metric_name         = "Throttles"
  dimensions          = local.lambda_dims
  statistic           = "Sum"
  period              = 60
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  treat_missing_data  = "notBreaching"
  tags                = merge(local.tags, { "nightwatch:severity" = "high" })
}

resource "aws_cloudwatch_metric_alarm" "dynamodb_throttles" {
  alarm_name          = "${var.service}-dynamodb-throttles"
  alarm_description   = "DynamoDB table ${var.table_name} throttled reads or writes."
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  evaluation_periods  = 1
  datapoints_to_alarm = 1
  treat_missing_data  = "notBreaching"
  tags                = merge(local.tags, { "nightwatch:severity" = "medium" })

  metric_query {
    id          = "throttled"
    expression  = "FILL(reads, 0) + FILL(writes, 0)"
    label       = "Throttled requests"
    return_data = true
  }

  metric_query {
    id = "reads"

    metric {
      namespace   = "AWS/DynamoDB"
      metric_name = "ReadThrottleEvents"
      dimensions  = { TableName = var.table_name }
      period      = 60
      stat        = "Sum"
    }
  }

  metric_query {
    id = "writes"

    metric {
      namespace   = "AWS/DynamoDB"
      metric_name = "WriteThrottleEvents"
      dimensions  = { TableName = var.table_name }
      period      = 60
      stat        = "Sum"
    }
  }
}
