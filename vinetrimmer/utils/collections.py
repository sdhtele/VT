import itertools
from typing import Any, Iterable, Iterator, Sequence, Tuple, Type, Union

import requests


class SSLSecurity:
    def __init__(self, level: int):
        """
        Reduce (or just change) the security level of python-requests SSL.

        For security, level is not defaulted to anything and must be provided. Security level will
        be returned to it's previous level once exited.

        Level 0:
            Everything is permitted. This retains compatibility with previous versions of OpenSSL.

        Level 1:
            The security level corresponds to a minimum of 80 bits of security. Any parameters
            offering below 80 bits of security are excluded. As a result RSA, DSA and DH keys
            shorter than 1024 bits and ECC keys shorter than 160 bits are prohibited. All export
            cipher suites are prohibited since they all offer less than 80 bits of security. SSL
            version 2 is prohibited. Any cipher suite using MD5 for the MAC is also prohibited.

        Level 2:
            Security level set to 112 bits of security. As a result RSA, DSA and DH keys shorter
            than 2048 bits and ECC keys shorter than 224 bits are prohibited. In addition to the
            level 1 exclusions any cipher suite using RC4 is also prohibited. SSL version 3 is
            also not allowed. Compression is disabled.

        Level 3:
            Security level set to 128 bits of security. As a result RSA, DSA and DH keys shorter
            than 3072 bits and ECC keys shorter than 256 bits are prohibited. In addition to the
            level 2 exclusions cipher suites not offering forward secrecy are prohibited. TLS
            versions below 1.1 are not permitted. Session tickets are disabled.

        Level 4:
            Security level set to 192 bits of security. As a result RSA, DSA and DH keys shorter
            than 7680 bits and ECC keys shorter than 384 bits are prohibited. Cipher suites using
            SHA1 for the MAC are prohibited. TLS versions below 1.2 are not permitted.

        Level 5:
            Security level set to 256 bits of security. As a result RSA, DSA and DH keys shorter
            than 15360 bits and ECC keys shorter than 512 bits are prohibited.
        """
        if not isinstance(level, int):
            raise ValueError(f"Level should be an int, not '{type(level).__class__.__name__}'")
        self.level = level

    def __enter__(self) -> None:
        self.old_ciphers = requests.packages.urllib3.util.ssl_.DEFAULT_CIPHERS
        requests.packages.urllib3.util.ssl_.DEFAULT_CIPHERS = f"DEFAULT:@SECLEVEL={self.level}"

    def __exit__(self, *exc: Any) -> None:
        requests.packages.urllib3.util.ssl_.DEFAULT_CIPHERS = self.old_ciphers


def as_lists(*args: Any) -> Iterator[Any]:
    """Converts any input objects to list objects."""
    for item in args:
        yield item if isinstance(item, list) else [item]


def as_list(*args: Any) -> list:
    """
    Convert any input objects to a single merged list object.

    Example:
        >>> as_list('foo', ['buzz', 'bizz'], 'bazz', 'bozz', ['bar'], ['bur'])
        ['foo', 'buzz', 'bizz', 'bazz', 'bozz', 'bar', 'bur']
    """
    return list(itertools.chain.from_iterable(as_lists(*args)))


def flatten(items: Any, ignore_types: Union[Type, Tuple[Type, ...]] = str) -> Iterator:
    """
    Flattens items recursively.

    Example:
    >>> list(flatten(["foo", [["bar", ["buzz", [""]], "bee"]]]))
    ['foo', 'bar', 'buzz', '', 'bee']
    >>> list(flatten("foo"))
    ['foo']
    >>> list(flatten({1}, set))
    [{1}]
    """
    if isinstance(items, (Iterable, Sequence)) and not isinstance(items, ignore_types):
        for i in items:
            yield from flatten(i, ignore_types)
    else:
        yield items


def merge_dict(source: dict, destination: dict) -> None:
    """Recursively merge Source into Destination in-place."""
    for key, value in source.items():
        if isinstance(value, dict):
            # get node or create one
            node = destination.setdefault(key, {})
            merge_dict(value, node)
        else:
            destination[key] = value
