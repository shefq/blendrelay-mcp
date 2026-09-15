"""Explicit allow rules for the two supported asset catalogues."""
from dataclasses import dataclass


class AssetError(ValueError):
    pass


@dataclass(frozen=True)
class AssetPolicy:
    poly_haven: bool = True
    poly_pizza: bool = True
    pizza_cc0: bool = True
    pizza_cc_by: bool = False
    max_download_mb: int = 200

    @classmethod
    def read(cls, value=None):
        value = value or {}
        defaults = cls()
        settings = {}
        for key in ('poly_haven', 'poly_pizza', 'pizza_cc0', 'pizza_cc_by'):
            item = value.get(key, getattr(defaults, key))
            if not isinstance(item, bool):
                raise AssetError('Provider controls must be booleans')
            settings[key] = item
        size = value.get('max_download_mb', 200)
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= 2048:
            raise AssetError('Maximum download size must be 1–2048 MB')
        return cls(**settings, max_download_mb=size)

    def check(self, record, cached=False):
        provider, licence = record.get('provider'), record.get('licence')
        if provider not in ('poly_haven', 'poly_pizza'):
            raise AssetError('Unsupported asset provider')
        # Provider switches govern network access. Cached assets remain available,
        # but the licence filters still apply to every import.
        if not cached and not getattr(self, provider):
            raise AssetError('This online asset provider is disabled in Asset Provider Policy.')
        if provider == 'poly_haven' and licence != 'CC0':
            raise AssetError('Poly Haven assets must have a verified CC0 licence')
        if provider == 'poly_pizza':
            if licence == 'CC-BY' and not self.pizza_cc_by:
                raise AssetError('This asset requires CC-BY attribution, which is disabled in Asset Provider Policy.')
            if licence == 'CC0' and not self.pizza_cc0:
                raise AssetError('Poly Pizza CC0 is disabled in Asset Provider Policy.')
            if licence not in ('CC0', 'CC-BY'):
                raise AssetError('Unknown or unsupported licence; asset was excluded')
            if licence == 'CC-BY' and not all(record.get(k) for k in ('creator', 'source_url', 'licence_url')):
                raise AssetError('CC-BY attribution details are incomplete')
