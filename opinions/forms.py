"""Public-facing forms for DocketDrift.

Currently just the StateRequest CTA form. The form intentionally accepts
free-text for state_name -- "California", "CA", and "Puerto Rico" all
hash to the same admin-side review queue, and the maintainer normalizes
when they see the request.
"""
from django import forms

from opinions.models import StateRequest


class StateRequestForm(forms.ModelForm):
    """Public form for requesting a state's appellate corpus be added.

    Honeypot field ``website`` is the bot-trap: bots fill every form
    field they see; humans don't see this one (CSS-hidden in the
    template). View rejects any submission with a non-empty website.
    """

    # Honeypot. Real users don't see it; bots fill it.
    website = forms.CharField(required=False, widget=forms.HiddenInput())

    class Meta:
        model = StateRequest
        fields = ["state_name", "email", "notes"]
        widgets = {
            "state_name": forms.TextInput(attrs={
                "placeholder": "California, Texas, Puerto Rico, etc.",
                "autocomplete": "off",
                "autofocus": "autofocus",
                "maxlength": 64,
            }),
            "email": forms.EmailInput(attrs={
                "placeholder": "Optional — we'll email when your state goes live",
                "autocomplete": "email",
            }),
            "notes": forms.Textarea(attrs={
                "placeholder": "Optional — anything you want to tell us",
                "rows": 3,
            }),
        }
        labels = {
            "state_name": "Which state?",
            "email": "Email (optional)",
            "notes": "Notes (optional)",
        }
