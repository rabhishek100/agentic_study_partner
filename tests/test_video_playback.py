"""A stored lecture plays through a signed, expiring link."""

import os
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from video.playback import (
    DEFAULT_TTL_SECONDS,
    EXPIRY_BUCKET_SECONDS,
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


class PlaybackStabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(
            os.environ, {"VIDEO_PLAYBACK_SECRET": "test-secret-value"}
        )
        self.environment.start()
        self.video, self.owner = uuid4(), uuid4()

    def tearDown(self) -> None:
        self.environment.stop()

    def test_repeated_requests_produce_the_same_link(self) -> None:
        """A changing URL reloads the player and loses the viewer's place.

        The workspace polls while a video is processing, so the link has to be
        the same string each time rather than a fresh token per response.
        """

        # Both calls are pinned mid-bucket. Taking the first reading from the
        # real clock made this fail on 6.7% of runs — the 120 seconds of every
        # 1800 where the pair straddles an expiry boundary — which is a flaky
        # test rather than a property of the link.
        aligned = 1_800_000_000 - (
            1_800_000_000 + DEFAULT_TTL_SECONDS
        ) % EXPIRY_BUCKET_SECONDS
        base = aligned + EXPIRY_BUCKET_SECONDS // 2

        with patch("video.playback.time.time", lambda: base):
            first = playback_url(video_id=self.video, owner_id=self.owner)
        with patch("video.playback.time.time", lambda: base + 120):
            second = playback_url(video_id=self.video, owner_id=self.owner)
        self.assertEqual(first, second)

    def test_the_link_changes_once_per_bucket_and_no_oftener(self) -> None:
        """What the grid actually promises, stated rather than assumed.

        The link is not immutable — it advances one step every half hour, and
        a poll that crosses a boundary reloads the player once. That is the
        cost of an expiring link and is worth pinning, because the test above
        would otherwise read as a promise the code never made.
        """

        aligned = 1_800_000_000 - (
            1_800_000_000 + DEFAULT_TTL_SECONDS
        ) % EXPIRY_BUCKET_SECONDS
        with patch("video.playback.time.time", lambda: aligned - 1):
            before = playback_url(video_id=self.video, owner_id=self.owner)
        with patch("video.playback.time.time", lambda: aligned):
            after = playback_url(video_id=self.video, owner_id=self.owner)
        self.assertNotEqual(before, after)

    def test_the_link_still_carries_a_usable_lifetime(self) -> None:
        token = sign_playback(video_id=self.video, owner_id=self.owner)
        self.assertEqual(verify_playback(token), (self.video, self.owner))
        # Still valid an hour from now, well past any single page visit.
        soon = time.time() + 3_600
        with patch("video.playback.time.time", lambda: soon):
            self.assertEqual(verify_playback(token), (self.video, self.owner))
