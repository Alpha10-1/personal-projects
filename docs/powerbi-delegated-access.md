# Reading Power BI as the person, not as the application

*Written for whoever administers the Azure tenant and the Power BI service.
It describes what this tracker would need in order to read Power BI on
behalf of a signed-in user. Nothing here is built yet — this is the request,
with enough detail to be assessed rather than guessed at.*

---

## The problem with what exists today

The tracker currently connects to Power BI with a **service principal**:
`PBI_TENANT_ID`, `PBI_CLIENT_ID`, `PBI_CLIENT_SECRET`, using the OAuth 2.0
client-credentials flow. It reads the list of reports and their refresh
state, and nothing else.

A service principal is an **application identity**. It has whatever access
it has been granted, independent of any person. That is fine for "when did
this report last refresh", which is metadata about the report rather than
anything inside the dataset. It is the wrong identity for anything else:

- **Row-level security is defined against a user principal.** RLS roles are
  assigned to users and groups. A service principal reading a dataset is
  not any of those users, so the filtering that makes RLS meaningful does
  not describe it. Depending on how the workspace and the dataset are set
  up, it either sees nothing or sees everything — never "what Alubisi is
  allowed to see".
- **The audit trail names the application, not the person.** Anything read
  is attributed to the app registration, which is the opposite of what you
  want when the question later is who saw what.

So: the credentials in `backend/.env` today cannot be used to read dataset
contents under a person's permissions, and no amount of care in this
codebase changes that. It is the identity that is wrong.

## What reading as the user requires

**The authorization code flow with PKCE**, so the person signs in to
Microsoft themselves and the tracker holds a token issued *to them*.

### 1. An app registration

A registration in the tenant (this can be the existing one, with additions,
or a separate one — a separate one is cleaner, because its permissions are
a different shape):

| Setting | Value |
|---|---|
| Supported account types | Single tenant |
| Redirect URI | `http://localhost:8000/powerbi/callback` — platform **Web** |
| Allow public client flows | No |
| Client secret | Yes, for the code-for-token exchange |

The redirect is localhost because the tracker runs on one machine and has
no hosted component. If it is ever hosted, this changes and so does the
security review that goes with it.

### 2. Delegated permissions, not application permissions

Under **API permissions → Power BI Service**, choose **Delegated**:

- `Report.Read.All`
- `Dataset.Read.All`
- `Workspace.Read.All`

Delegated permissions are the whole point: the effective access is the
intersection of what the app is allowed and what the signed-in person is
allowed. An *application* permission with the same name is the thing to
avoid — that is the service principal again, wearing a different hat.

Whether these need admin consent depends on tenant policy. If they do, that
is the decision to be made, and it should be made knowing the app can only
ever see what the person signing in can already see.

### 3. Tenant settings in the Power BI admin portal

- The security group containing the app must be allowed to use the Power BI
  REST APIs.
- **"Service principals can use Fabric APIs"** is *not* needed for this and
  should be left alone — that setting is about the identity we are
  deliberately moving away from.

### 4. What the tracker would then do

1. Send the person to `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize`
   with `scope=https://analysis.windows.net/powerbi/api/Report.Read.All offline_access`,
   a PKCE challenge and a state value.
2. Receive the code on the redirect, exchange it for an access token and a
   refresh token.
3. Store the refresh token **encrypted at rest**, or not at all and require
   a sign-in each session. On a single-user local tracker, requiring a
   sign-in is defensible and much simpler; a token sitting in a SQLite file
   next to the database is a credential in a file, and this codebase has
   already had one of those committed to a repository.
4. Call the REST API with that token. Everything read is then scoped by the
   person's own permissions, and RLS applies as designed.

### 5. What it still would not do

Executing queries against a dataset (`POST /datasets/{id}/executeQueries`)
is a further permission and a further decision. It returns **dataset rows**
— the actual data behind the report. That is the material this tracker is
now explicitly built never to send to a model, so if it is ever added, it
is for display to the signed-in person only.

## Where the line sits in the code

Independently of any of the above, and already in force:

- `app/privacy.py` refuses to send anything containing a Power BI report,
  workspace or dataset identifier to the model. It is checked on every
  outgoing call, and there is no setting that turns it off.
- No prompt-building code reads the `dashboards` table. That is the real
  protection; the check above exists for the day someone changes it without
  thinking it through.

So the sequencing is safe either way: delegated access can be added without
widening what reaches the model, because the two are enforced separately.

## What to ask for

> A single-tenant app registration with **delegated** `Report.Read.All`,
> `Dataset.Read.All` and `Workspace.Read.All` against the Power BI Service,
> a web redirect URI of `http://localhost:8000/powerbi/callback`, and the
> app's security group enabled for the Power BI REST APIs. No application
> permissions. Used by a single-user local tool to list reports and their
> refresh state under the signed-in user's own access.
