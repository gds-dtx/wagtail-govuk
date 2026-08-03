"""The Capability Framework welcome page.

Role and skill pages come from the public CSV exports, but the welcome page
is editorial prose that no export carries. Keeping it here lets
``import_capability_framework`` lay it down reproducibly, rather than someone
retyping it into the admin, and gives a later Strapi export one place to
replace.

Markup and wording follow the live service. ``{skills_url}`` is filled in
with the imported Skills A to Z page.
"""

WELCOME_HTML = """\
<h2 class="govuk-heading-l" id="how-to-use-this-framework">How to use this framework</h2>
<p class="govuk-body">Anyone can use this framework to:</p>
<ul class="govuk-list govuk-list--bullet">
<li class="govuk-body">learn what the different digital and data roles do in government</li>
<li class="govuk-body">understand what skills are needed at each role level</li></ul>
<p class="govuk-body">Professionals in Government Digital and Data can use this framework to:</p>
<ul class="govuk-list govuk-list--bullet">
<li class="govuk-body">identify skills that they can develop</li>
<li class="govuk-body">assess their current skill levels in preparation for performance and development conversations</li>
<li class="govuk-body">learn about the typical responsibilities and skills of their colleagues</li></ul>
<p class="govuk-body">Line managers and team leaders in government can use this framework to:</p>
<ul class="govuk-list govuk-list--bullet">
<li class="govuk-body">identify skill gaps in their teams and opportunities for development</li>
<li class="govuk-body">inform development goals and conversations</li>
<li class="govuk-body">forecast their organisation workforce needs, to make sure they have the right skills to achieve objectives</li></ul>
<p class="govuk-body">Hiring managers in government can use this framework to:</p>
<ul class="govuk-list govuk-list--bullet">
<li class="govuk-body">create effective and consistent job adverts</li>
<li class="govuk-body">assess the suitability of a candidate during interview</li></ul>
<h2 class="govuk-heading-l" id="skills-in-this-framework">Skills in this framework</h2>
<p class="govuk-body">Each role level (such as junior business analyst or senior business analyst) in this framework includes a list of required skills. Each skill is assigned one of 4 skill levels, reflecting the required proficiency: awareness, working, practitioner or expert.</p>
<p class="govuk-body">As you progress from one role level to the next, the proficiency required for each skill will typically increase (other than in instances where leadership positions no longer require day-to-day use of the skill).</p>
<p class="govuk-body">You can see the full list of skills and their definitions in the <a href="{skills_url}" class="govuk-link">Skills A to Z</a>.</p>
<table class="govuk-table homepage">
<caption class="govuk-table__caption govuk-table__caption--m sr-only">Skill level definitions</caption>
<thead class="govuk-table__head">
<tr class="govuk-table__row">
<th scope="col" class="govuk-table__header">Skill level definitions</th>
<th scope="col" class="govuk-table__header">What the level means</th></tr></thead>
<tbody class="govuk-table__body">
<tr class="govuk-table__row">
<td class="govuk-table__cell">
<p class="govuk-body govuk-!-font-size-19">Awareness</p>
<span class="govuk-visually-hidden">Awareness is the first of 4 ascending skill levels.</span>
<div class="skill-definitions__container" aria-hidden="true">
<div class="progress-bar__container" aria-hidden="true">
<div class="progress-bar__grey" aria-hidden="true"></div>
<div class="progress-bar__divider" aria-hidden="true"></div>
<div class="progress-bar__white" aria-hidden="true"></div>
<div class="progress-bar__divider" aria-hidden="true"></div>
<div class="progress-bar__white" aria-hidden="true"></div>
<div class="progress-bar__divider" aria-hidden="true"></div>
<div class="progress-bar__white" aria-hidden="true"></div></div></div></td>
<td class="govuk-table__cell govuk-!-width-two-thirds">
<p class="govuk-body">You can:</p>
<ul class="govuk-list govuk-list--bullet">
<li class="govuk-body">describe the fundamentals of the skill</li>
<li class="govuk-body">demonstrate basic knowledge of some of the skill's tools and techniques</li></ul></td></tr>
<tr class="govuk-table__row">
<td class="govuk-table__cell">
<p class="govuk-body govuk-!-font-size-19">Working</p>
<span class="govuk-visually-hidden">Working is the second of 4 ascending skill levels</span>
<div class="skill-definitions__container" aria-hidden="true">
<div class="progress-bar__container" aria-hidden="true">
<div class="progress-bar__grey" aria-hidden="true"></div>
<div class="progress-bar__divider" aria-hidden="true"></div>
<div class="progress-bar__grey" aria-hidden="true"></div>
<div class="progress-bar__divider" aria-hidden="true"></div>
<div class="progress-bar__white" aria-hidden="true"></div>
<div class="progress-bar__divider" aria-hidden="true"></div>
<div class="progress-bar__white" aria-hidden="true"></div></div></div></td>
<td class="govuk-table__cell govuk-!-width-two-thirds">
<p class="govuk-body">You can:</p>
<ul class="govuk-list govuk-list--bullet">
<li class="govuk-body">apply the skill with some support</li>
<li class="govuk-body">adopt the most appropriate tools and techniques</li></ul></td></tr>
<tr class="govuk-table__row">
<td class="govuk-table__cell">
<p class="govuk-body govuk-!-font-size-19">Practitioner</p>
<span class="govuk-visually-hidden">Practitioner is the third of 4 ascending skill levels</span>
<div class="skill-definitions__container" aria-hidden="true">
<div class="progress-bar__container" aria-hidden="true">
<div class="progress-bar__grey" aria-hidden="true"></div>
<div class="progress-bar__divider" aria-hidden="true"></div>
<div class="progress-bar__grey" aria-hidden="true"></div>
<div class="progress-bar__divider" aria-hidden="true"></div>
<div class="progress-bar__grey" aria-hidden="true"></div>
<div class="progress-bar__divider" aria-hidden="true"></div>
<div class="progress-bar__white" aria-hidden="true"></div></div></div></td>
<td class="govuk-table__cell govuk-!-width-two-thirds">
<p class="govuk-body">You can:</p>
<ul class="govuk-list govuk-list--bullet">
<li class="govuk-body">apply the skill without support</li>
<li class="govuk-body">determine and use the most appropriate tools and techniques</li>
<li class="govuk-body">share knowledge and experience of the skill</li></ul></td></tr>
<tr class="govuk-table__row">
<td class="govuk-table__cell last-cell">
<p class="govuk-body govuk-!-font-size-19">Expert</p>
<span class="govuk-visually-hidden">Expert is the fourth of 4 ascending skill levels</span>
<div class="skill-definitions__container" aria-hidden="true">
<div class="progress-bar__container" aria-hidden="true">
<div class="progress-bar__grey" aria-hidden="true"></div>
<div class="progress-bar__divider" aria-hidden="true"></div>
<div class="progress-bar__grey" aria-hidden="true"></div>
<div class="progress-bar__divider" aria-hidden="true"></div>
<div class="progress-bar__grey" aria-hidden="true"></div>
<div class="progress-bar__divider" aria-hidden="true"></div>
<div class="progress-bar__grey" aria-hidden="true"></div></div></div></td>
<td class="govuk-table__cell last-cell govuk-!-width-two-thirds">
<p class="govuk-body">You can:</p>
<ul class="govuk-list govuk-list--bullet">
<li class="govuk-body">lead and guide a team or organisation in the skill's best practice</li>
<li class="govuk-body">teach the skill's advanced tools and techniques</li></ul></td></tr></tbody></table>
<h3 class="govuk-heading-m">Skills for chief digital and data roles</h3>
<p class="govuk-body">Chief digital and data roles (such as chief technology officer or chief data officer) do not have role levels and so their skills do not have levels. Instead, each skill lists digital and data requirements together with examples of leadership needed to be effective in a Senior Civil Service role.</p>
<h2 class="govuk-heading-l" id="job-grades-in-this-framework">Job grades in this framework</h2>
<p class="govuk-body">Most levels of a role include one or two Civil Service job grades. These are the most common grades that the role level is performed at, based on government workforce data.</p>
<p class="govuk-body">The grade displayed is not mandatory for jobs at that role level across government. You can learn more about <a href="/job-grades" class="govuk-link">Civil Service job grades in this framework</a>.</p>
<hr class="govuk-section-break govuk-section-break--xl govuk-section-break--visible">
<h2 class="govuk-heading-l" id="support">Support</h2>
<p class="govuk-body">The Government Digital and Data Profession Capability Framework is maintained by the <a href="https://www.gov.uk/government/organisations/government-digital-service" class="govuk-link">Government Digital Service</a>.</p>
<p class="govuk-body">If you have a question or need support with using the framework, email the Capability Framework team at <a href="mailto:digitaldatacapabilityframework@dsit.gov.uk" class="govuk-link">digitaldatacapabilityframework@dsit.gov.uk</a>.</p>
<p class="govuk-body">You can also:</p>
<ul class="govuk-list govuk-list--bullet">
<li class="govuk-body">find out how to <a href="/propose-a-change" class="govuk-link">propose a change to the framework</a></li>
<li class="govuk-body">see what the team is working on and planning to do next on our <a href="/roadmap" class="govuk-link">roadmap</a></li></ul>
<p class="govuk-body">If you have a question about the wider Government Digital and Data profession, you can:</p>
<ul class="govuk-list govuk-list--bullet">
<li class="govuk-body">email <a href="mailto:gdd-profession-capability-team@dsit.gov.uk" class="govuk-link">gdd-profession-capability-team@dsit.gov.uk</a></li>
<li class="govuk-body">post in the <a href="https://ukgovernmentdigital.slack.com/archives/C838RAB0R" class="govuk-link">Government Digital and Data profession Slack channel</a></li></ul>
<hr class="govuk-section-break govuk-section-break--xl govuk-section-break--visible">
<h3 class="govuk-heading-m" id="capability-assessments">Capability assessments for Government Digital and Data professionals</h3>
<p class="govuk-body">Capability assessments for pay allowances are managed separately by each organisation. For support with your assessment, you will need to contact the internal team who manage the process for your organisation.</p>
<p class="govuk-body"></p>
<p class="govuk-body">If you are implementing or managing the Government Digital and Data Pay Framework in your organisation, you can contact the Digital Pay and Reward team at GDS for support:</p>
<ul class="govuk-list govuk-list--bullet">
<li class="govuk-body">email <a href="mailto:digital.pay@dsit.gov.uk" class="govuk-link">digital.pay@dsit.gov.uk</a></li>
<li class="govuk-body">message on the <a href="https://ukgovernmentdigital.slack.com/archives/C09LLNYRT7Z" class="govuk-link">Pay Forum Slack channel</a></li></ul>
<hr class="govuk-section-break govuk-section-break--xl govuk-section-break--visible">
<div class="govuk-inset-text">
<p class="govuk-body">The name of this guidance changed on 1 December 2023. Previously its name was ‘Digital, Data and Technology (DDaT) Profession Capability Framework’. This change reflected the <a href="https://www.gov.uk/government/news/digital-skills-rebrand-to-attract-top-tech-talent-to-civil-service" class="govuk-link">launch of the Government Digital and Data brand</a>.</p></div>
"""
