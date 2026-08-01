"""What code a running service is actually serving.

Deployment drift is invisible without this. One evening the worker ran five
commits behind the repository for hours, and the only way to find out was to
compare a deployment timestamp against a git log by eye. A service that can
say what it is running turns that into a question with an answer.

The value is baked into the image at build time. Reading git at runtime would
report the repository the container was built from, which is not the same claim
and is usually absent from the container anyway.
"""

import os


UNKNOWN = "unknown"


def build_revision() -> str:
    """The commit this image was built from, or `unknown` outside a build."""

    return os.getenv("BUILD_REVISION", "").strip() or UNKNOWN


def build_time() -> str:
    """When this image was built, or `unknown`."""

    return os.getenv("BUILD_TIME", "").strip() or UNKNOWN
