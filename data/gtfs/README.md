# GTFS Feeds

Drop one GTFS zip per signup period into the matching folder:

```
data/gtfs/may25/<your_feed>.zip
data/gtfs/aug25/<your_feed>.zip
data/gtfs/jan26/<your_feed>.zip
data/gtfs/may26/<your_feed>.zip
```

Folder names must match the `SIGNUPS` list at the top of
`scripts/generate_dummy_data.py`. Filenames don't matter — the script
picks up the first `.zip` it finds in each folder.

To run the demo without real feeds, run `python scripts/make_sample_gtfs.py`
to populate these folders with synthetic data.
