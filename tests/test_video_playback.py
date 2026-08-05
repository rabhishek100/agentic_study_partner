"""A stored lecture plays through a signed, expiring link."""

import os
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from video.playback import (
    PlaybackTokenError,
    playback_url,
    sign_playback,
    verify_playback,
)


class PlaybackTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(
            os.environ, {"VIDEO_PLAYBACK_SECRET": "test-secret-value"}
        )
        self.environment.start()
        self.video, self.owner = uuid4(), uuid4()

    def tearDown(self) -> None:
        self.environment.stop()

    def test_a_signed_link_names_exactly_one_video_and_owner(self) -> None:
        token = sign_playback(video_id=self.video, owner_id=self.owner)
        self.assertEqual(verify_playback(token), (self.video, self.owner))

    def test_a_tampered_or_foreign_token_is_refused(self) -> None:
        token = sign_playback(video_id=self.video, owner_id=self.owner)
        for broken in (token[:-2], token.replace(".", "") , "", "a.b"):
            with self.assertRaises(PlaybackTokenError):
                verify_playback(broken)
        # A token signed with a different secret must not verify: the link is
        # a capability for these bytes, not a bearer pass.
        with patch.dict(os.environ, {"VIDEO_PLAYBACK_SECRET": "another-secret"}):
            with self.assertRaises(PlaybackTokenError):
                verify_playback(token)

    def test_an_expired_link_stops_working(self) -> None:
        token = sign_playback(
            video_id=self.video, owner_id=self.owner, ttl_seconds=60
        )
        later = time.time() + 3_600
        with patch("video.playback.time.time", lambda: later):
            with self.assertRaises(PlaybackTokenError):
                verify_playback(token)

    def test_the_url_is_relative_and_carries_the_token(self) -> None:
        url = playback_url(video_id=self.video, owner_id=self.owner)
        self.assertTrue(url.startswith(f"/api/videos/{self.video}/stream?token="))

    def test_without_a_secret_there_is_no_link_rather_than_a_crash(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(playback_url(video_id=self.video, owner_id=self.owner))


if __name__ == "__main__":
    unittest.main()
