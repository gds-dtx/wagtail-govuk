from django import forms

from govuk.models import Feedback


class FeedbackForm(forms.ModelForm):
    class Meta:
        model = Feedback
        fields = ["feedback_type", "comments", "referrer"]
        labels = {
            "feedback_type": "Type of feedback",
            "comments": "Comments",
        }
        help_texts = {
            "comments": None,
        }
        widgets = {
            "feedback_type": forms.Select(attrs={"class": "govuk-select"}),
            "comments": forms.Textarea(attrs={"class": "govuk-textarea", "rows": 8}),
            "referrer": forms.HiddenInput(),
        }
