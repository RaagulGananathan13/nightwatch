# A monthly cost budget with email alerts. Budgets that only alert are free.
#
# Three alerts: the first cent of actual spend (1% of $1), actual spend passing the limit, and
# AWS forecasting that the month will pass the limit. Billing data lags by up to a day.

resource "aws_budgets_budget" "this" {
  name         = var.name
  budget_type  = "COST"
  limit_amount = format("%.2f", var.limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "cost_filter" {
    for_each = length(var.services) > 0 ? [var.services] : []

    content {
      name   = "Service"
      values = cost_filter.value
    }
  }

  dynamic "notification" {
    for_each = {
      first_cent = { type = "ACTUAL", percent = 1 }
      over_limit = { type = "ACTUAL", percent = 100 }
      forecast   = { type = "FORECASTED", percent = 100 }
    }

    content {
      notification_type          = notification.value.type
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value.percent
      threshold_type             = "PERCENTAGE"
      subscriber_email_addresses = [var.alert_email]
    }
  }
}
