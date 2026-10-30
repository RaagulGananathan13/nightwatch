# Phase 8, F15: the MCP server. It runs on an engineer's machine, not in AWS, and switches to
# this role so their AI assistant can read exactly what NightWatch's investigator can, and
# nothing more. Only the users named in var.mcp_users may switch to it; every call it makes is
# also written to NightWatch's audit log.

resource "aws_iam_role" "mcp" {
  name                 = "nightwatch-mcp"
  path                 = "/nightwatch/"
  description          = "MCP server (spec F15): the investigator's read-only reach, for engineers' AI assistants"
  permissions_boundary = var.boundary_policy_arn
  assume_role_policy   = data.aws_iam_policy_document.mcp_trust.json
  max_session_duration = 3600
  tags                 = var.tags
}

data "aws_iam_policy_document" "mcp_trust" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = var.mcp_users
    }
  }
}

resource "aws_iam_role_policy" "mcp" {
  name   = "nightwatch-mcp"
  role   = aws_iam_role.mcp.id
  policy = data.aws_iam_policy_document.mcp.json
}

data "aws_iam_policy_document" "mcp" {
  source_policy_documents = [data.aws_iam_policy_document.read_only_tools.json]

  statement {
    # Incidents, evidence, reports and memory. PutItem only because every call is audited.
    sid       = "NightWatchState"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:Query"]
    resources = [aws_dynamodb_table.this.arn, "${aws_dynamodb_table.this.arn}/index/*"]
  }
}
