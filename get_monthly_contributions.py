import argparse
import datetime
import json
import os
from string import Template
import sys
import tempfile
import time
from typing import List, NamedTuple, Optional

import requests
import urllib3  # type: ignore


class JSONEncoderWithSets(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, set):
            return list(obj)
        return vars(obj)


PR_QUERY = Template("""
{
  search(first: 100, after: $cursor, query: "repo:$org_name/$repo_name is:pr created:>=$last_updated", type: ISSUE) {
    pageInfo {
      hasNextPage,
      endCursor
    },
    nodes {
      ... on PullRequest {
        id,
        number,
        createdAt,
        author {
          login
        },
        reviews(first: 100, after: $reviews_cursor) {
          pageInfo {
            hasNextPage,
            endCursor
          },
          nodes {
            author {
              login
            },
            submittedAt,
          }
        },
        comments(first: 100, after: $comments_cursor) {
          pageInfo {
            hasNextPage,
            endCursor
          },
          nodes {
            author {
              login
            },
            createdAt,
          },
        },
      },
    },
  }
}
""")

ISSUE_QUERY = Template("""
{
  search(first: 100, after: $cursor, query: "repo:$org_name/$repo_name is:issue created:>=$last_updated", type: ISSUE) {
    pageInfo {
      hasNextPage,
      endCursor
    },
    nodes {
      ... on Issue {
        id,
        number,
        createdAt,
        author {
          login
        },
        comments(first: 100, after: $comments_cursor) {
          pageInfo {
            hasNextPage,
            endCursor
          },
          nodes {
            author {
              login
            },
            createdAt,
          },
        },
      },
    },
  }
}
""")

ORG_QUERY = Template("""
{
  organization(login: "$org_name") {
    repositories(first: 100, after: $cursor) {
      pageInfo {
        hasNextPage,
        endCursor
      },
      nodes {
        defaultBranchRef {
          name
        },
        name,
        isArchived
      }
    }
  }
}
""")

def graphql_query(query: str, token: Optional[str]) -> dict:
    headers = {'Authorization': f'Bearer {token}'} if token else None

    while True:
        try:
            response = requests.post(
                'https://api.github.com/graphql',
                json={'query': query},
                headers=headers)
        except (ValueError, urllib3.exceptions.InvalidChunkLength, urllib3.exceptions.ProtocolError, requests.exceptions.ChunkedEncodingError):
            # We've seen this happen with urllib3 response.py throwing the following exception:
            # Traceback (most recent call last):
            #   File "/usr/lib/python3.10/site-packages/urllib3/response.py", line 697, in _update_chunk_length
            #     self.chunk_left = int(line, 16)
            # ValueError: invalid literal for int() with base 16: b''
            print('Failed HTTP call, sleeping for 10 seconds and trying again')
            time.sleep(10)
            continue

        if response.status_code != 200:
            print('GitHub GraphQL query failed with code {}; sleeping 1 minute.'.format(response.status_code))
            time.sleep(1 * 60)
            continue

        return response.json()


def parse_github_time(value: str) -> datetime.datetime:
    return datetime.datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ')


class Review:
    def __init__(self, login: str, submitted_at: str):
        self.login = login
        self.submitted_at_string = submitted_at
        self.submitted_at = parse_github_time(submitted_at)

    def __repr__(self):
        return '%s (%s)' % (self.login, self.submitted_at_string)


class Comment:
    def __init__(self, login: str, created_at: str):
        self.login = login
        self.created_at_string = created_at
        self.created_at = parse_github_time(created_at)

    def __repr__(self):
        return '%s (%s)' % (self.login, self.created_at_string)


class PullRequest:
    def __init__(self, login: str, created_at: str):
        self.login = login
        self.created_at_string = created_at
        self.created_at = parse_github_time(created_at)
        self.reviews: List[Review] = []
        self.comments: List[Comment] = []

    def add_review(self, login: str, submitted_at: str):
        self.reviews.append(Review(login, submitted_at))

    def add_comment(self, login: str, created_at: str):
        self.comments.append(Comment(login, created_at))

    def __repr__(self):
        return '%s (%s) @ %s @ %s' % (self.login, self.created_at_string, str(self.reviews), str(self.comments))


def query_prs(org_name: str, repo_name: str, token: Optional[str], last_updated: str) -> dict:
    cursor = 'null'
    reviews_cursor = 'null'
    comments_cursor = 'null'
    has_next_page = True
    pull_requests = {}
    while has_next_page:
        pr_query = PR_QUERY.substitute(
            org_name=org_name,
            repo_name=repo_name,
            cursor=cursor,
            reviews_cursor=reviews_cursor,
            last_updated=last_updated,
            comments_cursor=comments_cursor)
        response = graphql_query(pr_query, token)
        results = response['data']['search']
        for pr in results['nodes']:
            pr_author = pr['author']
            # A PR author can be None if the account was deleted.
            if pr_author is None:
                continue

            pr_id = pr['id']
            pull_requests[pr_id] = PullRequest(pr_author['login'], pr['createdAt'])

            reviews = pr['reviews']
            for review in reviews['nodes']:
                # A review author can be None if the account was deleted.
                if review['author'] is None:
                    continue

                # If the login you are using happens to have a started, but
                # uncompleted review, then it shows up in the list of reviews
                # with a "submittedAt" as "None".  Just skip these.
                if review['submittedAt'] is None:
                    continue

                pull_requests[pr_id].add_review(review['author']['login'], review['submittedAt'])

            reviews_page_info = reviews['pageInfo']
            if reviews_page_info['hasNextPage']:
                reviews_cursor = '"%s"' % (reviews_page_info['endCursor'])
            else:
                reviews_cursor = 'null'

                comments = pr['comments']
                for comment in comments['nodes']:
                    # A review author can be None if the account was deleted.
                    if comment['author'] is None:
                        continue

                    pull_requests[pr_id].add_comment(comment['author']['login'], comment['createdAt'])

                comments_page_info = comments['pageInfo']
                if comments_page_info['hasNextPage']:
                    comments_cursor = '"%s"' % (comments_page_info['endCursor'])
                else:
                    comments_cursor = 'null'

        page_info = results['pageInfo']
        if reviews_cursor != 'null':
            # If there are more reviews, move the reviews cursor forward but not
            # the overall page_info
            has_next_page = True
        elif comments_cursor != 'null':
            # If there are more comments, move the comments cursor forward but not
            # the overall page_info
            has_next_page = True
        elif page_info['hasNextPage']:
            # If there are more PRs, move the overall cursor forward
            cursor = '"%s"' % (page_info['endCursor'])
            has_next_page = True
        else:
            has_next_page = False

    return pull_requests


class Issue:
    def __init__(self, login: str, created_at: str):
        self.login = login
        self.created_at_string = created_at
        self.created_at = parse_github_time(created_at)
        self.comments: List[Comment] = []

    def add_comment(self, login: str, created_at: str):
        self.comments.append(Comment(login, created_at))

    def __repr__(self):
        return '%s (%s) @ %s' % (self.login, self.created_at_string, str(self.comments))


def query_issues(org_name: str, repo_name: str, token: Optional[str], last_updated: str) -> dict:
    cursor = 'null'
    reviews_cursor = 'null'
    comments_cursor = 'null'
    has_next_page = True
    issues = {}
    while has_next_page:
        issue_query = ISSUE_QUERY.substitute(
            org_name=org_name,
            repo_name=repo_name,
            cursor=cursor,
            reviews_cursor=reviews_cursor,
            last_updated=last_updated,
            comments_cursor=comments_cursor)
        response = graphql_query(issue_query, token)
        results = response['data']['search']
        for issue in results['nodes']:
            issue_author = issue['author']
            # An issue author can be None if the account was deleted.
            if issue_author is None:
                continue

            issue_id = issue['id']
            issues[issue_id] = Issue(issue_author['login'], issue['createdAt'])

            comments = issue['comments']
            for comment in comments['nodes']:
                # A review author can be None if the account was deleted.
                if comment['author'] is None:
                    continue

                issues[issue_id].add_comment(comment['author']['login'], comment['createdAt'])

            comments_page_info = comments['pageInfo']
            if comments_page_info['hasNextPage']:
                comments_cursor = '"%s"' % (comments_page_info['endCursor'])
            else:
                comments_cursor = 'null'

        page_info = results['pageInfo']
        if comments_cursor != 'null':
            # If there are more comments, move the comments cursor forward but not
            # the overall page_info
            has_next_page = True
        elif page_info['hasNextPage']:
            # If there are more issues, move the overall cursor forward
            cursor = '"%s"' % (page_info['endCursor'])
            has_next_page = True
        else:
            has_next_page = False

    return issues


def query_org_repos_from_name(org_name: str, token: Optional[str]) -> list:
    cursor = 'null'
    has_next_page = True
    organization_repos = []
    while has_next_page:
        org_query = ORG_QUERY.substitute(
            org_name=org_name,
            cursor=cursor)
        response = graphql_query(org_query, token)
        results = response['data']['organization']['repositories']
        for repo in results['nodes']:
            if repo['defaultBranchRef'] is None:
                # This is likely an empty repository, so just skip it
                continue

            organization_repos.append(repo['name'])

        page_info = results['pageInfo']
        cursor = '"%s"' % (page_info['endCursor'])
        has_next_page = page_info['hasNextPage']

    return organization_repos


class ContributionReportOptions(NamedTuple):
    orgs: List[str]
    repos: List[str]
    token: str
    output_file: str


def parse_args() -> ContributionReportOptions:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-t', '--token',
        default=None,
        help='Github access token. Optional but you might get rate limited without it')
    parser.add_argument(
        '-o', '--orgs',
        nargs='+',
        required=False,
        default=[],
        help='Report contributions only to repos these github organizations')
    parser.add_argument(
        '--repos',
        nargs='+',
        required=False,
        default=[],
        help='Report contributions to these specific GitHub repositories (must be of the form org/repo)')
    parser.add_argument(
        '--output-file',
        default='monthly_activity.yaml',
        help='Output data to this filename (defaults to "monthly_activity.yaml")')

    parsed = parser.parse_args()

    return ContributionReportOptions(
        token=parsed.token,
        orgs=parsed.orgs,
        repos=parsed.repos,
        output_file=parsed.output_file)


class AuthorCounts:
    def __init__(self, today: datetime.datetime):
        self.prs_by_month = {}
        self.reviews_by_month = {}
        self.pr_comments_by_month = {}
        self.issues_by_month = {}
        self.issue_comments_by_month = {}
        for year in range(2013, today.year):
            for month in range(1, 12+1):
                stringdate = '%d-%02d' % (year, month)
                self.prs_by_month[stringdate] = 0
                self.reviews_by_month[stringdate] = 0
                self.pr_comments_by_month[stringdate] = 0
                self.issues_by_month[stringdate] = 0
                self.issue_comments_by_month[stringdate] = 0

        for month in range(1, today.month+1):
            stringdate = '%d-%02d' % (today.year, month)
            self.prs_by_month[stringdate] = 0
            self.reviews_by_month[stringdate] = 0
            self.pr_comments_by_month[stringdate] = 0
            self.issues_by_month[stringdate] = 0
            self.issue_comments_by_month[stringdate] = 0

    def increment_prs(self, date: datetime.datetime, activity: int):
        datemonth = '%d-%02d' % (date.year, date.month)
        self.prs_by_month[datemonth] += activity

    def increment_reviews(self, date: datetime.datetime, activity: int):
        datemonth = '%d-%02d' % (date.year, date.month)
        self.reviews_by_month[datemonth] += activity

    def increment_pr_comments(self, date: datetime.datetime, activity: int):
        datemonth = '%d-%02d' % (date.year, date.month)
        self.pr_comments_by_month[datemonth] += activity

    def increment_issues(self, date: datetime.datetime, activity: int):
        datemonth = '%d-%02d' % (date.year, date.month)
        self.issues_by_month[datemonth] += activity

    def increment_issue_comments(self, date: datetime.datetime, activity: int):
        datemonth = '%d-%02d' % (date.year, date.month)
        self.issue_comments_by_month[datemonth] += activity

    def __repr__(self) -> str:
        return 'PRs opened: %s, PR reviews: %s, PR comments: %s' % (str(self.prs_by_month), str(self.reviews_by_month), str(self.pr_comments_by_month))


def load_existing_data(filename: str, today: datetime.datetime) -> dict:
    output_data: dict = {
        'last_updated': today.strftime('%Y-%m-%d'),
        'repos_visited': set(),
        'author_contrib': {},
    }

    if not os.path.exists(filename):
        return output_data

    # Read in our previous data, so we can skip anything we've already
    # successfully fetched.
    with open(filename, 'r') as infp:
        data = json.load(infp)

        output_data['last_updated'] = data['last_updated']

        output_data['repos_visited'] = set(data['repos_visited'])

        for author, values in data['author_contrib'].items():
            output_data['author_contrib'][author] = AuthorCounts(today)

            for datestr, activity in data['author_contrib'][author]['prs_by_month'].items():
                date = datetime.datetime.strptime(datestr, '%Y-%m').date()
                output_data['author_contrib'][author].increment_prs(date, activity)

            for datestr, activity in data['author_contrib'][author]['reviews_by_month'].items():
                date = datetime.datetime.strptime(datestr, '%Y-%m').date()
                output_data['author_contrib'][author].increment_reviews(date, activity)

            for datestr, activity in data['author_contrib'][author]['pr_comments_by_month'].items():
                date = datetime.datetime.strptime(datestr, '%Y-%m').date()
                output_data['author_contrib'][author].increment_pr_comments(date, activity)

            for datestr, activity in data['author_contrib'][author]['issues_by_month'].items():
                date = datetime.datetime.strptime(datestr, '%Y-%m').date()
                output_data['author_contrib'][author].increment_issues(date, activity)

            for datestr, activity in data['author_contrib'][author]['issue_comments_by_month'].items():
                date = datetime.datetime.strptime(datestr, '%Y-%m').date()
                output_data['author_contrib'][author].increment_issue_comments(date, activity)

    return output_data


def write_out_data(filename, data):
    # Write the data to a temporary file, then do a rename so the update is atomic
    full_path = os.path.realpath(filename)
    dirname = os.path.dirname(full_path)

    with tempfile.NamedTemporaryFile(mode='w+', dir=dirname, delete=False) as f:
        json.dump(data, f, indent=4, cls=JSONEncoderWithSets)

    os.rename(f.name, full_path)


def main():
    options = parse_args()

    if not options.repos and not options.orgs:
        print('At least one repo or organization must be specified')
        return 1

    today = datetime.date.today()

    # Load in the existing data, if it exists
    output_data = load_existing_data(options.output_file, today)

    # Setup the list of repositories to query, based on both the passed
    # individual repositories and the organizations (from which we will
    # query all of the repositories).
    org_repos = {}

    for repo in options.repos:
        split = repo.split('/')
        if len(split) != 2:
            print('repositories must be of the form "org/repo"')
            return 1

        org = split[0]
        repo = split[1]

        if not org in org_repos:
            org_repos[org] = set()

        org_repos[org].add(repo)

    for org in options.orgs:
        if not org in org_repos:
            org_repos[org] = set()

        print('Querying repositories in org: %s' % (org))
        org_repos[org].update(query_org_repos_from_name(org, options.token))

    # Now iterate over each repository, getting data on all of the PRs and
    # storing it in the authors_contrib dict.
    for org_name, repos in org_repos.items():
        print('Organization: %s' % (org_name))
        for repo_name in repos:
            print('  Repository: %s' % (repo_name))
            full_repo_name = '%s/%s' % (org_name, repo_name)
            if full_repo_name not in output_data['repos_visited']:
                last_updated = '2013-01-01'
            else:
                last_updated = output_data['last_updated']

            prs = query_prs(org_name, repo_name, options.token, last_updated)
            for pr_id, pr in prs.items():
                if not pr.login in output_data['author_contrib']:
                    output_data['author_contrib'][pr.login] = AuthorCounts(today)

                output_data['author_contrib'][pr.login].increment_prs(pr.created_at, 1)

                for review in pr.reviews:
                    if not review.login in output_data['author_contrib']:
                        output_data['author_contrib'][review.login] = AuthorCounts(today)

                    output_data['author_contrib'][review.login].increment_reviews(review.submitted_at, 1)

                for comment in pr.comments:
                    if not comment.login in output_data['author_contrib']:
                        output_data['author_contrib'][comment.login] = AuthorCounts(today)
                    output_data['author_contrib'][comment.login].increment_pr_comments(comment.created_at, 1)

            issues = query_issues(org_name, repo_name, options.token, last_updated)
            for issue_id, issue in issues.items():
                if not issue.login in output_data['author_contrib']:
                    output_data['author_contrib'][issue.login] = AuthorCounts(today)
                output_data['author_contrib'][issue.login].increment_issues(issue.created_at, 1)

                for comment in issue.comments:
                    if not comment.login in output_data['author_contrib']:
                        output_data['author_contrib'][comment.login] = AuthorCounts(today)
                    output_data['author_contrib'][comment.login].increment_issue_comments(comment.created_at, 1)

            output_data['repos_visited'].add(full_repo_name)

            # We re-dump all of the data after every repository; that way
            # if we get interrupted somehow, we can pick up where we left off.
            write_out_data(options.output_file, output_data)

    # Write it out one last time to update the 'last_updated' time
    output_data['last_updated'] = today.strftime('%Y-%m-%d')
    write_out_data(options.output_file, output_data)

    return 0


if __name__ == '__main__':
    sys.exit(main())
