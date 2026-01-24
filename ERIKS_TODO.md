# Erik's To-Do List

**Created:** January 23, 2026
**Context:** Infrastructure build complete (17:10 by Antigravity). These tasks require your input.

---

## Setup Tasks (Do These First)

### E1: Google OAuth Credentials
- [ ] Create Google Cloud project (or use existing)
- [ ] Enable Google OAuth 2.0 API
- [ ] Create OAuth client credentials (Web application type)
- [ ] Set authorized redirect URI: `http://localhost:8000/auth/callback`
- [ ] Add to `.env`:
  ```
  GOOGLE_CLIENT_ID=your-client-id
  GOOGLE_CLIENT_SECRET=your-client-secret
  GOOGLE_AUTHORIZED_EMAILS=your-email@gmail.com
  ```

### E2: Newsletter Service Setup
- [ ] Choose email service (Buttondown, ConvertKit, Resend, or custom SMTP)
- [ ] Create account and get API credentials
- [ ] Add to `.env`:
  ```
  NEWSLETTER_SERVICE=buttondown
  NEWSLETTER_API_KEY=your-api-key
  ```

### E3: Discord Webhook (Verify)
- [ ] Confirm `DISCORD_WEBHOOK_URL` is in `.env` (should already be done)

---

## Manual Testing Checklist

Once E1 is done, test the admin dashboard:

### Admin Dashboard
- [ ] Visit `http://localhost:8000/admin/` (or wherever it's hosted)
- [ ] Verify Google OAuth login redirects correctly
- [ ] Verify only your whitelisted email can access
- [ ] Check dashboard shows recipe counts by status

### Recipe Management
- [ ] View list of pending recipes
- [ ] Click into a recipe detail/preview
- [ ] Test Approve button (moves pending → approved)
- [ ] Test Reject button (moves to rejected with notes)
- [ ] Test Publish button (triggers PublishingPipeline)

### Publishing Pipeline
- [ ] Verify published recipe appears in `src/recipes/{slug}/`
- [ ] Verify `src/recipes.json` updated
- [ ] Verify `src/sitemap.xml` regenerated
- [ ] Verify Discord notification sent on publish
- [ ] Verify Vercel deploy triggered (git push)

### Newsletter (after E2)
- [ ] Test email signup form on homepage
- [ ] Verify invalid emails rejected
- [ ] Verify subscription stored
- [ ] Check admin can view subscriber list

---

## Quick Enhancements (Backlog)

- [ ] Add dates to recipe cards (created_at / published_at)
- [ ] Featured recipe section (Antigravity adding now)
- [ ] Email signup form (Antigravity adding now)

---

## How to Run Admin Dashboard

```bash
# From the muffinpanrecipes project root:
uv run uvicorn backend.admin.app:app --reload
```

Then visit: http://localhost:8000/admin/

---

*Get to this when you have time. The build is done - these are just config and verification.*
