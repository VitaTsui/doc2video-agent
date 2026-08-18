


def version() -> str:
    """This build's version, from the installed package metadata.

    Hard-coding it in each surface is how the API came to report 0.3.0 while
    the project was on 0.6.0: nothing fails when they disagree, so nobody
    notices until a client trusts one of them.
    """
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _version

    try:
        return _version("doc2video-agent")
    except PackageNotFoundError:  # running from a source tree without an install
        return "0.0.0+source"
