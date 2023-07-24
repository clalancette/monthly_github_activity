import argparse
import datetime
import json
import pandas as pd
import sys
from typing import List, NamedTuple, Optional

import matplotlib.pyplot as plt
import matplotlib.dates as dates
import numpy as np


def IsoDate(value: str) -> datetime.date:
    """Validate and translate an argparse input into a datetime.date from ISO format."""
    return datetime.datetime.strptime(value, '%Y-%m').date()


class ContributionReportOptions(NamedTuple):
    authors: List[str]
    anonymize: bool
    since: datetime.date
    input_file: str


def parse_args() -> ContributionReportOptions:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-a', '--authors',
        nargs='+',
        required=False,
        default=[],
        help='Report contributions for these github usernames')
    parser.add_argument(
        '--anonymize',
        required=False,
        default=False,
        action='store_true',
        help='Anonymize the usernames when plotting')
    parser.add_argument(
        '-s', '--since',
        type=IsoDate,
        default=datetime.date.today() - datetime.timedelta(days=30),
        help='Report contributions on or after this date '
             '(format YYYY-MM).  Defaults to 1 month ago')
    parser.add_argument(
        '--input-file',
        default='monthly_activity.yaml',
        help='Input data from this filename (defaults to "monthly_activity.yaml")')

    parsed = parser.parse_args()

    if not parsed.authors:
        print('No --authors specified, the results might be huge...')

    return ContributionReportOptions(
        authors=parsed.authors,
        anonymize=parsed.anonymize,
        since=parsed.since,
        input_file=parsed.input_file)


def main():
    options = parse_args()

    with open(options.input_file, 'r') as infp:
        output_data = json.load(infp)
        author_contrib = output_data['author_contrib']

    author_number = 1
    overall_contributions = {}

    for author, contrib in author_contrib.items():
        if options.authors and author not in options.authors:
            continue

        total_contributions = {}

        for month, activity in contrib['prs_by_month'].items():
            date = IsoDate(month)
            if date < options.since:
                continue

            if not month in total_contributions:
                total_contributions[month] = 0
            total_contributions[month] += activity

            if not month in overall_contributions:
                overall_contributions[month] = 0
            overall_contributions[month] += activity

        for month, activity in contrib['reviews_by_month'].items():
            date = IsoDate(month)
            if date < options.since:
                continue

            if not month in total_contributions:
                total_contributions[month] = 0
            total_contributions[month] += activity

            if not month in overall_contributions:
                overall_contributions[month] = 0
            overall_contributions[month] += activity

        for month, activity in contrib['pr_comments_by_month'].items():
            date = IsoDate(month)
            if date < options.since:
                continue

            if not month in total_contributions:
                total_contributions[month] = 0
            total_contributions[month] += activity

            if not month in overall_contributions:
                overall_contributions[month] = 0
            overall_contributions[month] += activity

        for month, activity in contrib['issues_by_month'].items():
            date = IsoDate(month)
            if date < options.since:
                continue

            if not month in total_contributions:
                total_contributions[month] = 0
            total_contributions[month] += activity

            if not month in overall_contributions:
                overall_contributions[month] = 0
            overall_contributions[month] += activity

        for month, activity in contrib['issue_comments_by_month'].items():
            date = IsoDate(month)
            if date < options.since:
                continue

            if not month in total_contributions:
                total_contributions[month] = 0
            total_contributions[month] += activity

            if not month in overall_contributions:
                overall_contributions[month] = 0
            overall_contributions[month] += activity

        munged_author = author
        if options.anonymize:
            munged_author = f'Developer {author_number}'
        author_number += 1

        plt.plot(list(total_contributions.keys()), list(total_contributions.values()), label=munged_author)
        plt.legend()

    # Plot the overall contribution trend
    df = pd.DataFrame({'Date': overall_contributions.keys(), 'Value': overall_contributions.values()})
    x_num = dates.datestr2num(df['Date'])
    #trend = np.polyfit(x_num, df['Value'], 1)
    trend = np.polyfit(x_num, df['Value'], 2)
    fit = np.poly1d(trend)
    plt.plot(df['Date'], fit(x_num), 'r--')

    plt.show()

    return 0


if __name__ == '__main__':
    sys.exit(main())
