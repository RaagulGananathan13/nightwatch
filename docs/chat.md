# Set up Discord and Slack

After [deploying](deploy.md), NightWatch posts incident cards to Discord through the channel
webhook. This page adds the parts that need a chat application:

- **approval buttons**, so an approver can accept or reject a proposed fix;
- **`/nw` commands**, to ask NightWatch questions from chat;
- **Slack**, with the same cards, buttons and commands.

Every button click and command is signed by Discord or Slack and checked by NightWatch. Requests
more than 5 minutes old are refused. Only the approvers listed for a service can decide on its
fixes, and every refusal is audited.

## Discord

### 1. Create the application and bot

1. In the [Discord Developer Portal](https://discord.com/developers/applications), select
   **New Application** and name it, for example `NightWatch`.
2. On **General Information**, copy the **Public Key**. It is not a secret.
3. On **Bot**, select **Reset Token** and copy the token. It is a secret, so store it straight
   away:

    === "PowerShell"

        ```powershell
        $token = Read-Host "Paste the bot token"; aws ssm put-parameter --profile nightwatch --name /nightwatch/discord/bot_token --type SecureString --value $token --overwrite; Remove-Variable token
        ```

    === "bash (macOS, Linux)"

        ```bash
        read -rsp "Paste the bot token: " token; echo; aws ssm put-parameter --profile nightwatch --name /nightwatch/discord/bot_token --type SecureString --value "$token" --overwrite; unset token
        ```

4. On **OAuth2 → URL Generator**, select the `bot` and `applications.commands` scopes, and the
   **View Channels**, **Send Messages** and **Read Message History** permissions. Open the
   generated URL and add the bot to your server.

### 2. Tell NightWatch where to post and who may approve

In Discord, turn on **User Settings → Advanced → Developer Mode**. You can then right-click a
channel, a user or a server to copy its id.

1. In `infra/envs/dev/terraform.tfvars`, set:

    ```hcl
    discord_channel_id = "<your #incidents channel id>"
    discord_public_key = "<the application's public key>"
    ```

2. In `nightwatch/services.yaml`, list each approver's Discord user id:

    ```yaml
    approvers: ["<your Discord user id>"]
    ```

3. Deploy the change, then connect Discord to it:

    ```bash
    make plan
    make apply
    make discord-endpoint                                   # points button clicks at NightWatch
    make discord-commands DISCORD_GUILD_ID=<your server id>  # registers /nw in your server
    ```

    `make discord-endpoint` saves the interactions function's URL as the application's
    **Interactions Endpoint URL**. Discord tests the URL before it accepts it.

### 3. Try it

Type `/nw status` in your server. NightWatch lists the open incidents.

| Command | What it does |
|---|---|
| `/nw status` | Lists open incidents |
| `/nw ack <incident>` | Takes an open incident, for example `/nw ack INC-12` |
| `/nw update <incident>` | Drafts a customer update and a leadership summary for an approver to publish |
| `/nw ask <question>` | Investigates a question read-only, for example "why is checkout slow?" |

When a diagnosis matches a runbook, the incident's card gets an approval card with **Approve**
and **Reject** buttons, and NightWatch mentions the approvers.

## Slack (optional)

Slack gets the same cards, buttons and `/nw` commands as Discord
([ADR 0022](decisions/0022-slack-adapter.md)). A free workspace is enough.

1. **Create the app from the manifest.** At [api.slack.com/apps](https://api.slack.com/apps),
   select **Create New App → From a manifest**. Paste
   [`infra/slack-app-manifest.yaml`](https://github.com/RaagulGananathan13/nightwatch/blob/main/infra/slack-app-manifest.yaml),
   with both `<INTERACTIONS_URL>` placeholders replaced by the output of:

    ```bash
    terraform -chdir=infra/envs/dev output -raw discord_interactions_url
    ```

    Discord and Slack share this one endpoint.

2. **Install it.** On **OAuth & Permissions**, select **Install to Workspace**, then copy the
   **Bot User OAuth Token**, which starts with `xoxb-`.
3. **Copy the signing secret** from **Basic Information → App Credentials**.
4. **Store both:**

    === "PowerShell"

        ```powershell
        $token = Read-Host "Paste the Slack bot token"; aws ssm put-parameter --profile nightwatch --name /nightwatch/slack/bot_token --type SecureString --value $token --overwrite; Remove-Variable token
        $secret = Read-Host "Paste the Slack signing secret"; aws ssm put-parameter --profile nightwatch --name /nightwatch/slack/signing_secret --type SecureString --value $secret --overwrite; Remove-Variable secret
        ```

    === "bash (macOS, Linux)"

        ```bash
        read -rsp "Paste the Slack bot token: " token; echo; aws ssm put-parameter --profile nightwatch --name /nightwatch/slack/bot_token --type SecureString --value "$token" --overwrite; unset token
        read -rsp "Paste the Slack signing secret: " secret; echo; aws ssm put-parameter --profile nightwatch --name /nightwatch/slack/signing_secret --type SecureString --value "$secret" --overwrite; unset secret
        ```

5. **Choose the channel.** Create an `#incidents` channel and type `/invite @NightWatch` in it.
   Copy its channel id from the channel name, **About**, at the bottom. Set it as
   `slack_channel_id` in `terraform.tfvars`.
6. **Add approvers.** Copy each approver's member id (profile, **⋮**, **Copy member ID**) into
   `slack_approvers` in `nightwatch/services.yaml`.
7. Run `make plan` and `make apply`.

!!! note "Type Slack commands"
    Type `/nw status` rather than pasting it. Slack sends pasted, formatted text as a message,
    not as a command.

## Stop every fix at once

The kill switch stops every fix, in Discord and Slack alike. Incidents and diagnoses carry on.
See [day-to-day operation](operations.md#the-kill-switch).
