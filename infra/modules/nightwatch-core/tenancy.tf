# Tenant isolation (spec section 13): every key NightWatch writes starts with TENANT#<tenant>,
# its index keys included. Each role's access to the table carries a dynamodb:LeadingKeys
# condition, so IAM itself refuses a read or write of another tenant's items, whatever the
# code asks for. No role may Scan, which LeadingKeys can't limit.

locals {
  tenant_keys = ["TENANT#${var.tenant_id}", "TENANT#${var.tenant_id}#*"]
}
