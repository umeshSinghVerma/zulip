import subprocess
from argparse import ArgumentParser
from typing import Any, Dict

import requests
from django.conf import settings
from django.core.management.base import CommandError
from django.utils.crypto import get_random_string
from requests.models import Response
from typing_extensions import override

from zerver.lib.management import ZulipBaseCommand, check_config
from zerver.lib.remote_server import PushBouncerSession

if settings.DEVELOPMENT:
    SECRETS_FILENAME = "zproject/dev-secrets.conf"
else:
    SECRETS_FILENAME = "/etc/zulip/zulip-secrets.conf"


class Command(ZulipBaseCommand):
    help = """Register a remote Zulip server for push notifications."""

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--agree_to_terms_of_service",
            action="store_true",
            help="Agree to the Zulipchat Terms of Service: https://zulip.com/policies/terms.",
        )
        parser.add_argument(
            "--rotate-key",
            action="store_true",
            help="Automatically rotate your server's zulip_org_key",
        )

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.DEVELOPMENT:
            check_config()

        if not settings.ZULIP_ORG_ID:
            raise CommandError(
                "Missing zulip_org_id; run scripts/setup/generate_secrets.py to generate."
            )
        if not settings.ZULIP_ORG_KEY:
            raise CommandError(
                "Missing zulip_org_key; run scripts/setup/generate_secrets.py to generate."
            )
        if settings.PUSH_NOTIFICATION_BOUNCER_URL is None:
            if settings.DEVELOPMENT:
                settings.PUSH_NOTIFICATION_BOUNCER_URL = (
                    settings.EXTERNAL_URI_SCHEME + settings.EXTERNAL_HOST
                )
            else:
                raise CommandError(
                    "Please uncomment PUSH_NOTIFICATION_BOUNCER_URL "
                    "in /etc/zulip/settings.py (remove the '#')"
                )

        request = {
            "zulip_org_id": settings.ZULIP_ORG_ID,
            "zulip_org_key": settings.ZULIP_ORG_KEY,
            "hostname": settings.EXTERNAL_HOST,
            "contact_email": settings.ZULIP_ADMINISTRATOR,
        }
        if options["rotate_key"]:
            request["new_org_key"] = get_random_string(64)

        print(
            "This command registers your server for the Mobile Push Notifications Service.\n"
            "Doing so will share basic metadata with the service's maintainers:\n\n"
            f"* This server's configured hostname: {request['hostname']}\n"
            f"* This server's configured contact email address: {request['contact_email']}\n"
            "* Metadata about each organization hosted by the server; see:\n\n"
            "    <https://zulip.readthedocs.io/en/latest/production/mobile-push-notifications.html#uploading-basic-metadata>\n\n"
            "Use of this service is governed by the Zulip Terms of Service:\n\n"
            "    <https://zulip.com/policies/terms>\n"
        )

        if not options["agree_to_terms_of_service"] and not options["rotate_key"]:
            tos_prompt = input(
                "Do you want to agree to the Zulip Terms of Service and proceed? [Y/n] "
            )
            print("")
            if not (
                tos_prompt.lower() == "y" or tos_prompt.lower() == "" or tos_prompt.lower() == "yes"
            ):
                # Exit without registering; no need to print anything
                # special, as the "n" reply to the query is clear
                # enough about what happened.
                return

        response = self._request_push_notification_bouncer_url(
            "/api/v1/remotes/server/register", request
        )

        if response.json()["created"]:
            print(
                "Your server is now registered for the Mobile Push Notification Service!\n"
                "Return to the documentation for next steps."
            )
        else:
            if options["rotate_key"]:
                print(f"Success! Updating {SECRETS_FILENAME} with the new key...")
                subprocess.check_call(
                    [
                        "crudini",
                        "--set",
                        SECRETS_FILENAME,
                        "secrets",
                        "zulip_org_key",
                        request["new_org_key"],
                    ]
                )
            print("Mobile Push Notification Service registration successfully updated!")

    def _request_push_notification_bouncer_url(self, url: str, params: Dict[str, Any]) -> Response:
        assert settings.PUSH_NOTIFICATION_BOUNCER_URL is not None
        registration_url = settings.PUSH_NOTIFICATION_BOUNCER_URL + url
        session = PushBouncerSession()
        try:
            response = session.post(registration_url, params=params)
        except requests.RequestException:
            raise CommandError(
                "Network error connecting to push notifications service "
                f"({settings.PUSH_NOTIFICATION_BOUNCER_URL})",
            )
        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            # Report nice errors from the Zulip API if possible.
            try:
                content_dict = response.json()
            except Exception:
                raise e

            raise CommandError("Error: " + content_dict["msg"])

        return response
