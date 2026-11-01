# The Slack adapter: the same incident cards, approval buttons and /nw commands as Discord.
#
#   ingest, report, postmortem, cost ─► Slack (chat.postMessage / chat.update, bot token)
#   Slack clicks and /nw commands ────► nightwatch-interactions (same URL as Discord; HMAC check)
#
# Off until var.slack_channel_id is set. The bot token and signing secret are SecureStrings that
# the owner stores in SSM by hand; Terraform only names them.

locals {
  slack_on           = var.slack_channel_id != ""
  slack_token_param  = local.slack_on ? "/nightwatch/slack/bot_token" : ""
  slack_secret_param = local.slack_on ? "/nightwatch/slack/signing_secret" : ""
  slack_token_arn    = "arn:aws:ssm:${local.region}:${local.account}:parameter/nightwatch/slack/bot_token"
  slack_secret_arn   = "arn:aws:ssm:${local.region}:${local.account}:parameter/nightwatch/slack/signing_secret"
}
