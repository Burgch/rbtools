"""Tests for the rbt review command."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock

import kgb

from rbtools.commands.review import AddDiffComment
from rbtools.config import RBToolsConfig
from rbtools.testing import TestCase
from rbtools.testing.api.transport import URLMapTransport


class AddDiffCommentTests(kgb.SpyAgency, TestCase):
    """Tests for the ``rbt review add-diff-comment`` subcommand.

    These focus on locating the file to comment on within a diff. The list of
    files in a diff is paginated by the Review Board API (25 files per page by
    default), so the command must walk *every* page when matching the requested
    filename.
    """

    #: Base URL for the test server.
    SERVER_URL = 'https://reviews.example.com/'

    #: The ID of the review request used in these tests.
    REVIEW_REQUEST_ID = 123

    #: The diff revision used in these tests.
    DIFF_REVISION = 1

    def _build_command(
        self,
        *,
        filename: str,
    ) -> AddDiffComment:
        """Return an ``add-diff-comment`` subcommand ready to run.

        Args:
            filename (str):
                The filename to search for in the diff.

        Returns:
            rbtools.commands.review.AddDiffComment:
            The configured subcommand instance.
        """
        options = argparse.Namespace(
            review_request_id=str(self.REVIEW_REQUEST_ID),
            diff_revision=self.DIFF_REVISION,
            filename=filename,
            line=42,
            num_lines=1,
            text='This needs a mutex.',
            open_issue=False,
            markdown=True)

        return AddDiffComment(options=options, config=RBToolsConfig())

    def _make_file_payload(
        self,
        file_id: int,
        dest_file: str,
    ) -> dict:
        """Return an item payload for a single file in a diff.

        Args:
            file_id (int):
                The value of the file's ``id`` field. This is the
                ``filediff_id`` the comment should be posted against.

            dest_file (str):
                The destination path of the file.

        Returns:
            dict:
            The file diff item payload.
        """
        file_url = (
            f'{self.SERVER_URL}api/review-requests/{self.REVIEW_REQUEST_ID}/'
            f'diffs/{self.DIFF_REVISION}/files/{file_id}/'
        )

        return {
            'id': file_id,
            'source_file': dest_file,
            'source_revision': 'abc123',
            'dest_file': dest_file,
            'dest_detail': '',
            'links': {
                'self': {
                    'href': file_url,
                    'method': 'GET',
                },
            },
        }

    def _make_paginated_files(
        self,
        *,
        page1_files: list[dict],
        page2_files: list[dict],
    ):
        """Return the first page of a two-page file diff list resource.

        This registers both pages in a fresh transport. The first page carries
        a ``next`` link pointing at the second page, so that walking the list
        with :py:attr:`~rbtools.api.resource.base.ListResource.all_items` will
        fetch the second page from the transport.

        Args:
            page1_files (list of dict):
                The file item payloads for the first page.

            page2_files (list of dict):
                The file item payloads for the second page.

        Returns:
            rbtools.api.resource.FileDiffListResource:
            The first page of the file diff list.
        """
        files_url = (
            f'/api/review-requests/{self.REVIEW_REQUEST_ID}/'
            f'diffs/{self.DIFF_REVISION}/files/'
        )
        page1_url = f'{self.SERVER_URL}{files_url.lstrip("/")}'
        page2_path = f'{files_url}page2/'
        page2_url = f'{self.SERVER_URL}{page2_path.lstrip("/")}'

        list_mimetype = 'application/vnd.reviewboard.org.files+json'
        item_mimetype = 'application/vnd.reviewboard.org.file+json'

        transport = URLMapTransport(self.SERVER_URL)

        transport.add_url(
            url=files_url,
            mimetype=list_mimetype,
            headers={'Item-Content-Type': item_mimetype},
            payload={
                'files': page1_files,
                'links': {
                    'self': {
                        'href': page1_url,
                        'method': 'GET',
                    },
                    'next': {
                        'href': page2_url,
                        'method': 'GET',
                    },
                },
                'stat': 'ok',
                'total_results': len(page1_files) + len(page2_files),
            },
            extra_node_state={'list_key': 'files'})

        transport.add_url(
            url=page2_path,
            mimetype=list_mimetype,
            headers={'Item-Content-Type': item_mimetype},
            payload={
                'files': page2_files,
                'links': {
                    'self': {
                        'href': page2_url,
                        'method': 'GET',
                    },
                },
                'stat': 'ok',
                'total_results': len(page1_files) + len(page2_files),
            },
            extra_node_state={'list_key': 'files'})

        return transport.get_path(files_url)

    def _run_add_comment(
        self,
        command: AddDiffComment,
        files,
    ) -> MagicMock:
        """Run ``add_comment`` with a stubbed API root and review draft.

        Args:
            command (rbtools.commands.review.AddDiffComment):
                The subcommand to run.

            files (rbtools.api.resource.FileDiffListResource):
                The file diff list resource that ``diffset.get_files()`` should
                return.

        Returns:
            unittest.mock.MagicMock:
            The mock standing in for the diff comments list resource. Its
            ``create`` attribute records the call made to post the comment.
        """
        review_draft = MagicMock(name='review_draft')
        created_comment = \
            review_draft.get_diff_comments.return_value.create.return_value
        created_comment.id = 4478196
        created_comment.links.self.href = (
            f'{self.SERVER_URL}api/review-requests/{self.REVIEW_REQUEST_ID}/'
            f'reviews/1/diff-comments/4478196/'
        )

        diffset = MagicMock(name='diffset')
        diffset.get_files.return_value = files

        command.api_root = MagicMock(name='api_root')
        command.api_root.get_diff.return_value = diffset

        self.spy_on(command.get_review_draft,
                    op=kgb.SpyOpReturn(review_draft))

        command.add_comment('markdown')

        return review_draft.get_diff_comments.return_value

    def test_add_comment_to_file_on_second_page(self) -> None:
        """Testing review add-diff-comment finds a file past the first page

        This is a regression test. The API returns diff files 25 at a time, and
        the command previously only searched the first page, so a comment could
        not be posted to any file beyond the 25th. Without the fix this raises
        ``CommandError: Could not find a file ...``.
        """
        page1_files = [
            self._make_file_payload(file_id=i, dest_file=f'src/file{i}.cpp')
            for i in range(1, 26)
        ]
        page2_files = [
            self._make_file_payload(file_id=26,
                                    dest_file='src/ThreadPool.cpp'),
        ]

        command = self._build_command(filename='ThreadPool.cpp')
        files = self._make_paginated_files(page1_files=page1_files,
                                           page2_files=page2_files)

        diff_comments = self._run_add_comment(command, files)

        self.assertSpyCalled(command.get_review_draft)
        self.assertEqual(diff_comments.create.call_count, 1)
        self.assertEqual(diff_comments.create.call_args.kwargs['filediff_id'],
                         26)

    def test_add_comment_to_file_on_first_page(self) -> None:
        """Testing review add-diff-comment finds a file on the first page"""
        page1_files = [
            self._make_file_payload(file_id=i, dest_file=f'src/file{i}.cpp')
            for i in range(1, 26)
        ]
        page2_files = [
            self._make_file_payload(file_id=26,
                                    dest_file='src/ThreadPool.cpp'),
        ]

        command = self._build_command(filename='file7.cpp')
        files = self._make_paginated_files(page1_files=page1_files,
                                           page2_files=page2_files)

        diff_comments = self._run_add_comment(command, files)

        self.assertEqual(diff_comments.create.call_count, 1)
        self.assertEqual(diff_comments.create.call_args.kwargs['filediff_id'],
                         7)
