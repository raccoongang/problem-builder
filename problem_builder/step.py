# -*- coding: utf-8 -*-
#
# Copyright (c) 2014-2015 Harvard, edX & OpenCraft
#
# This software's license gives you freedom; you can copy, convey,
# propagate, redistribute and/or modify this program under the terms of
# the GNU Affero General Public License (AGPL) as published by the Free
# Software Foundation (FSF), either version 3 of the License, or (at your
# option) any later version of the AGPL published by the FSF.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU Affero
# General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program in a file in the toplevel directory called
# "AGPLv3".  If not, see <http://www.gnu.org/licenses/>.
#

import logging

from lazy.lazy import lazy

from xblock.core import XBlock
from xblock.fields import String, List, Scope
from xblock.fragment import Fragment
from xblockutils.resources import ResourceLoader
from xblockutils.studio_editable import (
    NestedXBlockSpec, StudioEditableXBlockMixin, StudioContainerWithNestedXBlocksMixin, XBlockWithPreviewMixin
)

from problem_builder.answer import AnswerBlock, AnswerRecapBlock
from problem_builder.mcq import MCQBlock, RatingBlock
from .message import (
    CompletedMentoringMessageShim, IncompleteMentoringMessageShim, OnReviewMentoringMessageShim
)
from problem_builder.mixins import EnumerableChildMixin, MessageParentMixin, StepParentMixin
from problem_builder.mrq import MRQBlock
from problem_builder.table import MentoringTableBlock


log = logging.getLogger(__name__)
loader = ResourceLoader(__name__)


# Make '_' a no-op so we can scrape strings
def _(text):
    return text


def _normalize_id(key):
    """
    Helper method to normalize a key to avoid issues where some keys have version/branch and others don't.
    e.g. self.scope_ids.usage_id != self.runtime.get_block(self.scope_ids.usage_id).scope_ids.usage_id
    """
    if hasattr(key, "for_branch"):
        key = key.for_branch(None)
    if hasattr(key, "for_version"):
        key = key.for_version(None)
    return key


class Correctness(object):
    CORRECT = 'correct'
    PARTIAL = 'partial'
    INCORRECT = 'incorrect'


class HtmlBlockShim(object):
    CATEGORY = 'html'
    STUDIO_LABEL = _(u"HTML")


@XBlock.needs('i18n')
class MentoringStepBlock(
        StudioEditableXBlockMixin, StudioContainerWithNestedXBlocksMixin, XBlockWithPreviewMixin,
        EnumerableChildMixin, StepParentMixin, XBlock
):
    """
    An XBlock for a step.
    """
    CAPTION = _(u"Step")
    STUDIO_LABEL = _(u"Mentoring Step")
    CATEGORY = 'sb-step'

    # Settings
    display_name = String(
        display_name=_("Step Title"),
        help=_('Leave blank to use sequential numbering'),
        default="",
        scope=Scope.content
    )

    # User state
    student_results = List(
        # Store results of student choices.
        default=[],
        scope=Scope.user_state
    )

    next_button_label = String(
        display_name=_("Next Button Label"),
        help=_("Customize the text of the 'Next' button."),
        default=_("Next Step")
    )

    editable_fields = ('display_name', 'show_title', 'next_button_label')

    @lazy
    def siblings(self):
        return self.get_parent().step_ids

    @property
    def is_last_step(self):
        parent = self.get_parent()
        return self.step_number == len(parent.step_ids)

    @property
    def allowed_nested_blocks(self):
        """
        Returns a list of allowed nested XBlocks. Each item can be either
        * An XBlock class
        * A NestedXBlockSpec

        If XBlock class is used it is assumed that this XBlock is enabled and allows multiple instances.
        NestedXBlockSpec allows explicitly setting disabled/enabled state, disabled reason (if any) and single/multiple
        instances
        """
        return [
            NestedXBlockSpec(AnswerBlock, boilerplate='studio_default'),
            MCQBlock, RatingBlock, MRQBlock, HtmlBlockShim,
            AnswerRecapBlock, MentoringTableBlock,
        ]

    @property
    def has_question(self):
        return any(getattr(child, 'answerable', False) for child in self.steps)

    @XBlock.json_handler
    def submit(self, submissions, suffix=''):
        log.info(u'Received submissions: {}'.format(submissions))

        # Submit child blocks (questions) and gather results
        submit_results = []
        for child in self.steps:
            if child.name and child.name in submissions:
                submission = submissions[child.name]
                child_result = child.submit(submission)
                submit_results.append([child.name, child_result])
                child.save()

        # Update results stored for this step
        self.reset()
        for result in submit_results:
            self.student_results.append(result)

        return {
            'message': 'Success!',
            'step_status': self.answer_status,
            'results': submit_results,
        }

    @XBlock.json_handler
    def get_results(self, queries, suffix=''):
        results = {}
        answers = dict(self.student_results)
        for question in self.steps:
            previous_results = answers[question.name]
            result = question.get_results(previous_results)
            results[question.name] = result

        # Add 'message' to results? Looks like it's not used on the client ...
        return {
            'results': results,
            'step_status': self.answer_status,
        }

    def reset(self):
        while self.student_results:
            self.student_results.pop()

    @property
    def answer_status(self):
        if all(result[1]['status'] == 'correct' for result in self.student_results):
            answer_status = Correctness.CORRECT
        elif all(result[1]['status'] == 'incorrect' for result in self.student_results):
            answer_status = Correctness.INCORRECT
        else:
            answer_status = Correctness.PARTIAL
        return answer_status

    def author_edit_view(self, context):
        """
        Add some HTML to the author view that allows authors to add child blocks.
        """
        local_context = dict(context)
        local_context['wrap_children'] = {
            'head': u'<div class="mentoring">',
            'tail': u'</div>'
        }
        fragment = super(MentoringStepBlock, self).author_edit_view(local_context)
        fragment.add_css_url(self.runtime.local_resource_url(self, 'public/css/problem-builder.css'))
        fragment.add_css_url(self.runtime.local_resource_url(self, 'public/css/problem-builder-edit.css'))
        fragment.add_css_url(self.runtime.local_resource_url(self, 'public/css/problem-builder-tinymce-content.css'))
        fragment.add_javascript_url(self.runtime.local_resource_url(self, 'public/js/util.js'))
        fragment.add_javascript_url(self.runtime.local_resource_url(self, 'public/js/step_edit.js'))
        fragment.initialize_js('StepEdit')
        return fragment

    def student_view(self, context=None):
        """ Student View """
        return self._render_view(context, 'student_view')

    def mentoring_view(self, context=None):
        """ Mentoring View """
        return self._render_view(context, 'mentoring_view')

    def _render_view(self, context, view):
        """ Actually renders a view """
        fragment = Fragment()
        child_contents = []

        for child_id in self.children:
            child = self.runtime.get_block(child_id)
            if child is None:  # child should not be None but it can happen due to bugs or permission issues
                child_contents.append(u"<p>[{}]</p>".format(self._(u"Error: Unable to load child component.")))
            else:
                child_fragment = self._render_child_fragment(child, context, view)

                fragment.add_frag_resources(child_fragment)
                child_contents.append(child_fragment.content)

        fragment.add_content(loader.render_template('templates/html/step.html', {
            'self': self,
            'title': self.display_name,
            'show_title': self.show_title,
            'child_contents': child_contents,
        }))

        fragment.add_javascript_url(self.runtime.local_resource_url(self, 'public/js/step.js'))
        fragment.initialize_js('MentoringStepBlock')

        return fragment


class ReviewStepBlock(MessageParentMixin, StudioContainerWithNestedXBlocksMixin, XBlockWithPreviewMixin, XBlock):
    """ A dedicated step for reviewing results for a mentoring block """
    CATEGORY = 'sb-review-step'
    STUDIO_LABEL = _("Review Step")

    display_name = String(
        default="Review Step"
    )

    @property
    def allowed_nested_blocks(self):
        """
        Returns a list of allowed nested XBlocks. Each item can be either
        * An XBlock class
        * A NestedXBlockSpec

        If XBlock class is used it is assumed that this XBlock is enabled and allows multiple instances.
        NestedXBlockSpec allows explicitly setting disabled/enabled state,
        disabled reason (if any) and single/multiple instances.
        """
        return [
            NestedXBlockSpec(CompletedMentoringMessageShim, boilerplate='completed'),
            NestedXBlockSpec(IncompleteMentoringMessageShim, boilerplate='incomplete'),
            NestedXBlockSpec(OnReviewMentoringMessageShim, boilerplate='on-review'),
        ]

    @XBlock.json_handler
    def get_assessment_message(self, grade, suffix):
        # Data passed as "grade" comes from "get_grade" handler of Step Builder (MentoringWithExplicitStepsBlock)
        complete = grade.get('complete')
        max_attempts_reached = grade.get('max_attempts_reached')
        return {
            'assessment_message': self.assessment_message(complete, max_attempts_reached)
        }

    def assessment_message(self, complete=None, max_attempts_reached=None):
        if complete is None and max_attempts_reached is None:
            parent = self.get_parent()
            complete = parent.complete
            max_attempts_reached = parent.max_attempts_reached
        if max_attempts_reached:
            assessment_message = self.get_message_content('on-review', or_default=True)
        else:
            if complete:  # All answers correct
                assessment_message = self.get_message_content('completed', or_default=True)
            else:
                assessment_message = self.get_message_content('incomplete', or_default=True)
        return assessment_message

    def mentoring_view(self, context=None):
        """ Mentoring View """
        return self._render_view(context)

    def student_view(self, context=None):
        """ Student View """
        return self._render_view(context)

    def studio_view(self, context=None):
        """ Studio View """
        return Fragment(u'<p>This is a preconfigured block. It is not editable.</p>')

    def _render_view(self, context):
        fragment = Fragment()
        fragment.add_content(loader.render_template('templates/html/review_step.html', {
            'self': self,
        }))
        fragment.add_javascript_url(self.runtime.local_resource_url(self, 'public/js/review_step.js'))
        fragment.initialize_js('ReviewStepBlock')
        return fragment

    def author_preview_view(self, context):
        return Fragment(
            u"<p>{}</p>".format(
                _(u"This block summarizes a student's performance on the parent Step Builder block.")
            )
        )

    def author_edit_view(self, context):
        """
        Add some HTML to the author view that allows authors to add child blocks.
        """
        context['wrap_children'] = {
            'head': u'<div class="mentoring">',
            'tail': u'</div>'
        }
        fragment = super(ReviewStepBlock, self).author_edit_view(context)
        fragment.add_css_url(self.runtime.local_resource_url(self, 'public/css/problem-builder-edit.css'))
        fragment.add_javascript_url(self.runtime.local_resource_url(self, 'public/js/util.js'))
        fragment.add_javascript_url(self.runtime.local_resource_url(self, 'public/js/review_step_edit.js'))
        fragment.initialize_js('ReviewStepEdit')
        return fragment
