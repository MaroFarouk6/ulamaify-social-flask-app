# Decisions and open integration gates

## Confirmed

- App URL: `https://social.ulamaify.org`.
- Existing Traefik uses Docker labels, external proxy network, `http` and `https` entrypoints, `https-redirect@file`, `security-headers@file`, and wildcard TLS for `ulamaify.org`.
- The app keeps its own login. Its router does not add `traefik-auth`; this does not alter n8n authentication.
- Google access uses the supplied OAuth client ID/secret plus a machine refresh token. Creators never authenticate to Google.
- Campaign Registry tab is `sheet1`, and row 1 contains the seven headers.
- The Sheet remains authoritative. Admins can add rows and toggle `ACTIVE`/`INACTIVE` in the app; valid direct Sheet changes appear after refresh.
- Facebook multi-image/carousel is a valid content-creation type even though the current Facebook n8n branch does not publish it yet.
- LinkedIn creation supports text-only, text plus one image, and a single PDF document. LinkedIn video and multi-image are disabled.
- App responsibility ends after Drive commit. No n8n or social API trigger exists.

## Central platform contract

| Platform | Valid in creation | Drive release in this build |
|---|---|---|
| Instagram | image, video, carousel (2–10 images), explicit Story | enabled |
| Facebook | text, image, video, multi-image/carousel | text/image/video enabled; carousel gated by `ENABLE_DRIVE_FACEBOOK_CAROUSEL` |
| LinkedIn | text, text + one image, one PDF | text/image enabled; PDF blocked pending exact n8n contract |
| YouTube | one MP4 video | enabled |

`document` is an internal detection value only. It is not written to `post.json`. The Drive values remain exactly `text`, `image`, `video`, `carousel`, and `story`.

## Items requiring target information

1. Verify the Facebook n8n multi-image branch consumes `post_type: "carousel"` and `media/slide1.jpg…`; only then set `ENABLE_DRIVE_FACEBOOK_CAROUSEL=true`.
2. Provide the future LinkedIn PDF branch’s exact `post.json` value and media naming contract. LinkedIn’s Documents API can upload PDFs, but the current app will not invent a new n8n contract.
3. Supply, as server files/environment only, the OAuth authorized-user file, Sheet ID, social root ID, and `01_CAMPAIGNS` folder ID.
4. Confirm the actual external Docker network name if it is not `proxy`. The provided labels otherwise mirror the n8n `http`/`https` and middleware names.
5. Execute live acceptance tests in a duplicate Sheet and isolated Drive root before enabling the production worker.

## Campaign Sheet contract

- Range: `'sheet1'!A:G`.
- Headers: `campaign_id`, `campaign_name`, `folder_name`, `type`, `status`, `start_date`, `end_date`.
- IDs and folder names are unique; status is exactly `ACTIVE` or `INACTIVE`; dates use `YYYY-MM-DD`.
- App additions append one row and then reread it for verification. Lost write responses are reconciled to avoid a duplicate retry.
- Status changes resolve by campaign ID, verify the displayed fingerprint, change only the status cell, and reread it.
- Deactivation never deletes Drive folders, submissions, or media.
- Direct Sheet edits are attributable through Sheets version history, not the application audit log.
