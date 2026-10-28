"""Explicit reference scope of the 50 existing brief cards.

This authoring registry preserves their original 168 topic references when a
learning objective is promoted to core.  It is not full teaching coverage;
expand a card's references only after checking its actual explanations.
"""

BRIEF_REFERENCE_GROUPS = {
    "j-number@七上": [
        "m7a.1.1",
        "m7a.1.2.1",
        "m7a.1.2.4",
        "m7a.1.2.5",
        "m7a.2.3.2",
        "m7a.2.3.3"
    ],
    "j-stat@七下": [
        "m7b.12.1.2"
    ],
    "j-transform@九下": [
        "m9b.33.2.1"
    ],
    "p-add-sub@一上": [
        "m1a.1.4",
        "m1a.1.5",
        "m1a.1.6",
        "m1a.1.7",
        "m1a.2.10",
        "m1a.2.11",
        "m1a.2.4",
        "m1a.2.7",
        "m1a.4.3",
        "m1a.5.1",
        "m1a.5.2",
        "m1a.5.3"
    ],
    "p-add-sub@一下": [
        "m1b.2.1",
        "m1b.2.2",
        "m1b.2.3",
        "m1b.3.7",
        "m1b.4.1",
        "m1b.4.2",
        "m1b.5.1",
        "m1b.5.2"
    ],
    "p-add-sub@二下": [
        "m2b.3.5",
        "m2b.4.1",
        "m2b.4.2",
        "m2b.4.3"
    ],
    "p-frac-dec@三上": [
        "m3a.6.1",
        "m3a.6.2",
        "m3a.6.3",
        "m3a.6.4",
        "m3a.6.7"
    ],
    "p-frac-dec@三下": [
        "m3b.6.1",
        "m3b.6.2",
        "m3b.6.3"
    ],
    "p-frac-dec@五下": [
        "m5b.2.1"
    ],
    "p-frac-dec@六上": [
        "m6a.3.1",
        "m6a.3.2"
    ],
    "p-frac-dec@四下": [
        "m4b.4.1"
    ],
    "p-measure@一下": [
        "m1b.6.4"
    ],
    "p-measure@三上": [
        "m3a.3.1",
        "m3a.3.2",
        "m3a.3.3",
        "m3a.3.5"
    ],
    "p-measure@二上": [
        "m2a.4.1",
        "m2a.4.2"
    ],
    "p-mul-div@三上": [
        "m3a.4.1",
        "m3a.4.2",
        "m3a.4.3",
        "m3a.4.4",
        "m3a.4.5",
        "m3a.4.6"
    ],
    "p-mul-div@三下": [
        "m3b.2.1",
        "m3b.2.2"
    ],
    "p-mul-div@二上": [
        "m2a.2.1",
        "m2a.2.2",
        "m2a.2.3",
        "m2a.2.5",
        "m2a.2.7",
        "m2a.3.1",
        "m2a.3.2",
        "m2a.3.3",
        "m2a.3.4",
        "m2a.3.5",
        "m2a.3.6",
        "m2a.5.1",
        "m2a.5.2",
        "m2a.5.3",
        "m2a.5.4",
        "m2a.5.5"
    ],
    "p-mul-div@二下": [
        "m2b.1.1",
        "m2b.1.2",
        "m2b.2.1"
    ],
    "p-mul-div@五下": [
        "m5b.1.1"
    ],
    "p-mul-div@四上": [
        "m4a.3.1",
        "m4a.3.2"
    ],
    "p-mul-div@四下": [
        "m4b.1.1"
    ],
    "p-num-recog@一上": [
        "m1a.1.1",
        "m1a.1.2",
        "m1a.1.3",
        "m1a.2.1",
        "m1a.2.2",
        "m1a.2.3",
        "m1a.2.9",
        "m1a.4.1",
        "m1a.4.2"
    ],
    "p-num-recog@一下": [
        "m1b.3.1",
        "m1b.3.2",
        "m1b.3.3",
        "m1b.3.4",
        "m1b.3.5",
        "m1b.3.6"
    ],
    "p-num-recog@二下": [
        "m2b.3.1",
        "m2b.3.2",
        "m2b.3.3",
        "m2b.3.4"
    ],
    "p-num-recog@六上": [
        "m6a.4.1",
        "m6a.4.2"
    ],
    "p-num-recog@四上": [
        "m4a.1.1",
        "m4a.1.2",
        "m4a.1.3"
    ],
    "p-ops@三上": [
        "m3a.2.1",
        "m3a.2.2",
        "m3a.2.3"
    ],
    "p-ops@二上": [
        "m2a.2.4"
    ],
    "p-ops@五上": [
        "m5a.5.1"
    ],
    "p-perim-area@三下": [
        "m3b.4.1",
        "m3b.4.3"
    ],
    "p-position@六上": [
        "m6a.1.1"
    ],
    "p-shape-recog@一上": [
        "m1a.3.1",
        "m1a.3.3"
    ],
    "p-shape-recog@一下": [
        "m1b.1.1",
        "m1b.1.2"
    ],
    "p-shape-recog@三上": [
        "m3a.1.1",
        "m3a.1.2",
        "m3a.1.3",
        "m3a.5.1",
        "m3a.5.2",
        "m3a.5.3",
        "m3a.5.4"
    ],
    "p-shape-recog@三下": [
        "m3b.1.1",
        "m3b.3.1"
    ],
    "p-shape-recog@二上": [
        "m2a.4.3"
    ],
    "p-shape-recog@五上": [
        "m5a.1.1",
        "m5a.4.1",
        "m5a.4.2",
        "m5a.4.3",
        "m5a.4.4"
    ],
    "p-shape-recog@六上": [
        "m6a.5.1",
        "m6a.5.4"
    ],
    "p-shape-recog@四上": [
        "m4a.2.1",
        "m4a.2.2",
        "m4a.5.1",
        "m4a.5.2"
    ],
    "p-shape-recog@四下": [
        "m4b.3.1"
    ],
    "p-stat@三下": [
        "m3b.5.1"
    ],
    "p-stat@二上": [
        "m2a.1.1",
        "m2a.1.2",
        "m2a.1.3"
    ],
    "p-stat@四下": [
        "m4b.6.1"
    ],
    "p-volume@五下": [
        "m5b.4.1"
    ],
    "p-word@一上": [
        "m1a.2.5",
        "m1a.2.6",
        "m1a.2.8",
        "m1a.3.2",
        "m1a.4.4",
        "m1a.5.4",
        "m1a.5.5"
    ],
    "p-word@一下": [
        "m1b.1.3",
        "m1b.2.4",
        "m1b.4.3",
        "m1b.6.1",
        "m1b.6.2",
        "m1b.6.3"
    ],
    "p-word@二上": [
        "m2a.2.6",
        "m2a.3.7",
        "m2a.4.4",
        "m2a.5.6"
    ],
    "p-word@二下": [
        "m2b.1.3",
        "m2b.2.2"
    ],
    "s-prob@必修二": [
        "mh2.9.1.1",
        "mh2.9.1.2"
    ],
    "s-prob@选必三": [
        "mh5.8.3.1"
    ]
}
