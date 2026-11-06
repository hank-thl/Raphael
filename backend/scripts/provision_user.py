"""Admin-only user provisioning. Preview by default; --apply permits creation."""
import argparse
import os
import sys


def ensure_user(email, auth, app):
    try:
        user = auth.get_user_by_email(email, app=app)
        created = False
    except auth.UserNotFoundError:
        user = auth.create_user(email=email, email_verified=False, disabled=False, app=app)
        created = True
    if user.disabled:
        raise RuntimeError("Existing user is disabled; no changes were made")
    return user, created


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    email = args.email.strip()
    if not email or any(c.isspace() for c in email) or email.count("@") != 1:
        parser.error("A valid email address is required")
    if args.project_id != os.getenv("FIREBASE_PROJECT_ID"):
        parser.error("--project-id must match FIREBASE_PROJECT_ID from backend/.env")
    print("Project:", args.project_id)
    print("Email:", email)
    if not args.apply:
        print("Preview: look up email; create a passwordless user only if missing.")
        print("No Firebase request made. Google identity linking is not performed.")
        return 0
    import firebase_admin
    from firebase_admin import auth, credentials
    app = None
    try:
        credential = credentials.ApplicationDefault()
        credential.get_credential()
        app = firebase_admin.initialize_app(credential, {"projectId": args.project_id}, name="provision")
        user, created = ensure_user(email, auth, app)
        print("Created:" if created else "Already exists:", user.uid)
        print("Google provider linked:", any(p.provider_id == "google.com" for p in user.provider_data))
        print("No password set; Google sign-in and signup restrictions still need verification.")
    except Exception as exc:
        print("Provisioning failed (" + type(exc).__name__ + "). Check permissions/configuration.", file=sys.stderr)
        return 1
    finally:
        if app is not None:
            firebase_admin.delete_app(app)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
