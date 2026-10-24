# COREBOUND release checks

## Automated checks included
- `python -m unittest discover -s tests -v` checks Python syntax, schema initialization/integrity, expected migration tables/columns, and key security guardrails without requiring a running web server.

## Manual application QA (requires Flask installed)
1. Login with valid credentials; check invalid credentials return a generic error.
2. Make 10 failed attempts from the same username or IP within 15 minutes; the next attempt should be throttled.
3. Confirm login/register/admin POST forms contain CSRF tokens and that a POST without a valid token is rejected.
4. Check that a reporter cannot open leader/member/admin pages and that a leader cannot access another territory's report.
5. Suspend/remove a user in another session and verify the next request ends the removed user's session.
6. Submit a report in a territory with a leader and members; confirm automatic assignment, notifications, deadline, status history, and assignment history.
7. Assign/reassign a report as a leader and confirm the assignment history contains previous/new assignees and actor.
8. Add progress text and evidence; verify only the assigned member/territory leader can add it and the reporter can view it.
9. Upload before/after photos; reject invalid file signatures and files above 5 MB.
10. Confirm overdue alerts occur once, and dashboard analytics/filter combinations work.
11. Confirm only `COREBOUND0950` can use Type 3 admin pages; verify the admin has only Dashboard & Users and Account Reports as main pages.
12. Verify `.flask_secret_key` and `.env` are not committed or shared, and `COOKIE_SECURE=1` is used only behind HTTPS.

## Validation limitations
The package was statically compiled and SQLite schema checks are automated. Browser-level tests require installing the dependencies and running the app in an environment with the project assets and a suitable database. Do not interpret these notes as a claim that a full browser test has already passed.
