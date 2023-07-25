# Monthly GitHub contributions

This is a collection of scripts to fetch and then graph monthly contributions to a GitHub repository.
It consists of two scripts: get_monthly_contributions.py and graph_monthly_contributions.py

## Prerequisites

The scripts are all written in Python (>= 3.8).
They also depend on the `requests` and `urllib3` module to fetch data, as well as `numpy` and `matplotlib` to graph the data.

## get_monthly_contributions.py

Example usage:

```
$ get_monthly_contributions.py --repos ros2/rosidl_core --orgs colcon -t <github_personal_token>
```

This script takes in a list of repositories and/or organizations, and outputs a YAML database consisting of a GitHub login mapped to how many contributions they had each month since 2013.
A "contribution" in this case counts as a newly opened pull request, a newly opened issue, a comment on an existing pull request, a comment on an existing issue, or a review on a pull request.

If an organization (`--orgs`) is given, then all repositories in that organization are examined.
To exclude certain ones, use the repository list instead (`--repos`).

While it is possible to run this script without the token, GitHub will severely rate-limit the connection.
It is highly recommended to create a personal token for use here (the token doesn't have to have any permissions).

The script has some intelligence in attempting not to download data it already has.
Thus if it finds a valid database (`monthly_activity.yaml`) before it starts, it will only fetch data that is missing.
If you really want to refetch everything, make sure to remove the `monthly_activity.yaml` file.

## graph_monthly_contributions.py

Example usage:

```
$ graph_monthly_contributions.py --authors clalancette --since 2022-01
```

This script graphs the data collected by the `get_monthly_contributions.py` script.

This script takes a list of authors (`--authors`), and a date (`--since`) to graph from.
It can also optionally anonymize the data with `--anonymize`.
When the script is launched, it will open up the database file and graph all of the contributions for all of the listed authors in the timeframe given.
It will also plot a "trend line", which is numpy polyfit of order 2 over all of the data.
