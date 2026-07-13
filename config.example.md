# Property Hunt — Personal Config (flat search)

> Copy this file to `config.md` and fill in your details.
> `config.md` is gitignored — your personal info stays local.

---

## About you

```
YOUR_NAME=Alex
YOUR_AGE=30
YOUR_PROFESSION=Software Engineer
YOUR_PROFILE_SUMMARY=Professional, clean and reliable, permanent contract
YOUR_WORK_POSTCODE=EC2A 1NT
```

## Target areas

Comma-separated, in priority order. Used to build per-area search URLs and to
match a listing's location for prioritisation.

```
PRIMARY_AREAS=Soho, Waterloo, Farringdon, Covent Garden, Southwark, Bloomsbury, Clerkenwell, Holborn, Barbican, Fitzrovia, Euston, St Pancras, SE1, WC1, EC1
SECONDARY_AREAS=
```

Zoopla only understands its own location names (e.g. *Waterloo*, *Farringdon*
and *Bloomsbury* aren't Zoopla locations, while *Clerkenwell* or *SE1* are);
areas it doesn't recognise are skipped for Zoopla only, with a console note.
Postcode districts (SE1, WC1, EC1) are handy: all platforms accept them and
they match listing postcodes directly.

## What you're looking for

```
PROPERTY_TYPE=flat
MIN_BEDROOMS=1
MAX_BEDROOMS=2
PRICE_MIN=3000
PRICE_MAX=4500
```

## Must-have / preferred criteria

```
# Outdoor space is a must (kept-but-flagged if not confirmed):
FEATURE_MUST=balcony, terrace
# Furnishing acceptable to you:
FURNISH_FILTER=unfurnished, part-furnished, flexible
# Preferred minimum internal size in sq ft (soft — boosts priority, never excludes):
MIN_SQFT=650
```

## Summary window

```
# How many hours a flat stays in the "recent" (catch-up) section of the run summary
RECENT_HOURS=24
```

## Move-in

```
MOVE_IN_DATE=flexible
```
