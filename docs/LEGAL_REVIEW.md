# Legal documents: status and open decisions

`/privacy`, `/terms` and `/minors` are **drafts prepared automatically. No lawyer has reviewed
them.** Each page shows a banner saying so, and amber notes at each open decision. Remove the
banner only after a lawyer has approved the text. The Kyrgyz text also needs a native
speaker's review.

The texts live in `apps/web/src/lib/i18n/{en,ru,ky}.ts` under `legal.*`. When the accepted
text changes, change `LEGAL_DOCUMENTS_VERSION` (`app/core/config.py`): each new account
records the version it accepted. Accounts created before migration 0011 have none; whether
they must accept the documents is an open decision below.

## Decisions for the owner and a lawyer

| Topic | Decision needed |
|---|---|
| Operator | Legal entity or person responsible for the data, and its details |
| Applicable law | Which law applies; legal basis for each processing purpose |
| Minors | Minimum age; age of independent consent; how guardian consent is obtained and proved; how a guardian requests access or deletion |
| Schools | Who is responsible for attendance data; agreements with schools; how families are told |
| Providers | Actual hosting, database and AI providers; storage countries; cross-border transfer terms |
| Retention | Periods for accounts, attendance, audit logs and backups |
| Deleted accounts | How long audit rows keep a deleted account's id and IP address; whether deletion must reach backups sooner |
| Contact | Working contact address; procedure and time for answering requests |
| Terms | Provider of the service; price; warranties and liability; governing law; disputes; notice of changes |
| Content | Wording on ownership of solutions and the rights needed to store them |
| Existing accounts | Whether accounts without a recorded acceptance must accept the documents |

## What the code does today

- Registration requires a ticked consent box and stores the document version and time.
  The platform asks for no age and cannot verify a guardian's consent.
- Export (`GET /users/me/export`) and deletion (`POST /users/me/delete`) are in the profile.
  Platform admins and the last admin of an organization cannot delete themselves.
- Password recovery is manual (no email provider): docs/SECURITY.md, "Account recovery".
