from django.contrib.auth.models import User
from django.test.testcases import TestCase


from moderation.helpers import automoderate
from moderation.constants import MODERATION_STATUS_APPROVED, MODERATION_STATUS_PENDING, MODERATION_STATUS_REJECTED
from moderation.moderator import GenericModerator
from moderation.utils import django_19

from tests.models import UserProfile, ModelWithVisibilityField, ModelWithModeratedFields
from tests.utils import setup_moderation, teardown_moderation


class CSRFMiddlewareTestCase(TestCase):
    fixtures = ['test_users.json']

    def setUp(self):
        setup_moderation([UserProfile])

    def tearDown(self):
        teardown_moderation()

    def test_csrf_token(self):
        profile = UserProfile(description='Profile for new user',
                              url='http://www.yahoo.com',
                              user=User.objects.get(username='user1'))

        profile.save()

        user = User.objects.get(username='admin')
        self.client.force_login(user)

        url = profile.moderated_object.get_admin_moderate_url()

        post_data = {'approve': 'Approve'}

        response = self.client.post(url, post_data)

        self.assertEqual(response.status_code, 302)

        profile = UserProfile.objects.get(pk=profile.pk)

        self.assertEqual(profile.moderated_object.status,
                         MODERATION_STATUS_APPROVED)


class AutomoderationRuntimeErrorRegressionTestCase(TestCase):
    fixtures = ['test_users.json', 'test_moderation.json']

    def setUp(self):
        setup_moderation([UserProfile])

        self.user = User.objects.get(username='admin')

    def tearDown(self):
        teardown_moderation()

    def test_RuntimeError(self):
        from moderation.helpers import automoderate

        profile = UserProfile.objects.get(user__username='moderator')
        profile.description = 'Change description'
        profile.save()
        profile.moderated_object.changed_by = self.user
        profile.moderated_object.save()
        automoderate(profile, self.user)
        profile.moderated_object.save()


class BypassOverwritesUpdatedObjectRegressionTestCase(TestCase):
    fixtures = ['test_users.json', 'test_moderation.json']

    def setUp(self):
        class BypassModerator(GenericModerator):
            visibility_column = 'is_public'
            bypass_moderation_after_approval = True

        setup_moderation([(ModelWithVisibilityField, BypassModerator)])
        self.user = User.objects.get(username='admin')

    def tearDown(self):
        teardown_moderation()

    def test_can_update_objects_with_bypass_enabled(self):
        obj = ModelWithVisibilityField.objects.create(test='initial')
        obj.save()

        # It's never been approved before, so it's now invisible
        self.assertEqual(
            [], list(ModelWithVisibilityField.objects.all()),
            "The ModelWithVisibilityField has never been approved and is now "
            "pending, so it should be hidden")
        # So approve it
        obj.moderated_object.approve(by=self.user, reason='test')
        # Now it should be visible, with the new description
        obj = ModelWithVisibilityField.objects.get()
        self.assertEqual('initial', obj.test)

        # Now change it again. Because bypass_moderation_after_approval is
        # True, it should still be visible and we shouldn't need to approve it
        # again.
        obj.test = 'modified'
        obj.save()
        obj = ModelWithVisibilityField.objects.get()
        self.assertEqual('modified', obj.test)

        # Admin does this after saving an object. Check that it doesn't undo
        # our changes.
        automoderate(obj, self.user)
        obj = ModelWithVisibilityField.objects.get()
        self.assertEqual('modified', obj.test)


class ApprovedRecordsRegressionTestCase(TestCase):
    fixtures = ['test_users.json']

    def setUp(self):
        setup_moderation([UserProfile, ModelWithModeratedFields])
        self.user = User.objects.get(username='admin')

    def tearDown(self):
        teardown_moderation()

    def get_approved_record(self):
        # Delete all records and create a fresh record in database
        ModelWithModeratedFields.objects.all().delete()
        obj = ModelWithModeratedFields.objects.create(moderated='moderated_value',
                                                             also_moderated='also_moderated_value',
                                                             unmoderated='unmoderated_value')
        obj.moderated_object.approve(by=self.user, reason='Initial Version')
        return ModelWithModeratedFields.objects.all().first()

    def test_change_moderated_field_only(self):
        obj = self.get_approved_record()
        obj.moderated = "moderated_value_1"
        obj.save()

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)
        self.assertEqual(MODERATION_STATUS_PENDING ,obj.moderated_object.status)

    def test_change_unmoderated_field_only(self):
        obj = self.get_approved_record()
        obj.unmoderated = "unmoderated_value_1"
        obj.save()

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('unmoderated_value_1', obj.unmoderated)
        self.assertEqual('unmoderated_value_1', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_APPROVED, obj.moderated_object.status)

    def test_change_moderated_and_unmoderated(self):
        obj = self.get_approved_record()
        obj.moderated = "moderated_value_1"
        obj.unmoderated = "unmoderated_value_1"
        obj.save()

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)

        self.assertEqual('unmoderated_value_1', obj.unmoderated)
        self.assertEqual('unmoderated_value_1', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

    def test_change_moderated_field_only_with_update_fields(self):
        obj = self.get_approved_record()
        obj.moderated = "moderated_value_1"
        obj.save(update_fields=['moderated'])

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

    def test_change_unmoderated_field_only_update_fields(self):
        obj = self.get_approved_record()
        obj.unmoderated = "unmoderated_value_1"
        obj.save(update_fields=['unmoderated'])

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('unmoderated_value_1', obj.unmoderated)
        self.assertEqual('unmoderated_value_1', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_APPROVED, obj.moderated_object.status)

    def test_change_moderated_and_unmoderated_update_fields(self):
        obj = self.get_approved_record()
        obj.moderated = "moderated_value_1"
        obj.unmoderated = "unmoderated_value_1"
        obj.save(update_fields=['moderated', 'unmoderated'])

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)

        self.assertEqual('unmoderated_value_1', obj.unmoderated)
        self.assertEqual('unmoderated_value_1', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

        obj.unmoderated = "unmoderated_value_2"
        obj.save(update_fields=['unmoderated'])
        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)

        self.assertEqual('unmoderated_value_2', obj.unmoderated)
        self.assertEqual('unmoderated_value_2', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)



    def test_without_any_change(self):
        obj = self.get_approved_record()
        obj.save()

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value', obj.moderated_object.changed_object.moderated)

        self.assertEqual('unmoderated_value', obj.unmoderated)
        self.assertEqual('unmoderated_value', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_APPROVED, obj.moderated_object.status)

    def test_with_previous_values(self):
        obj = self.get_approved_record()

        # Update the moderated field
        obj.moderated = "moderated_value_1"
        obj.save()

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

        # Update the moderated field again with the same value
        obj.moderated = "moderated_value_1"
        obj.save()
        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

        # Update the moderated with the database value
        obj.moderated = "moderated_value"
        obj.save()
        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value', obj.moderated_object.changed_object.moderated)
        #todo: should it be Pending or Approved?
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)


class PendingRecordsRegressionTestCase(TestCase):
    fixtures = ['test_users.json']

    def setUp(self):
        setup_moderation([UserProfile, ModelWithModeratedFields])
        self.user = User.objects.get(username='admin')

    def tearDown(self):
        teardown_moderation()

    def get_pending_record(self):
        # Delete all records and create a fresh record in database
        ModelWithModeratedFields.objects.all().delete()
        obj = ModelWithModeratedFields.objects.create(moderated='moderated_value',
                                                             also_moderated='also_moderated_value',
                                                             unmoderated='unmoderated_value')
        obj.moderated_object.approve(by=self.user, reason='Initial Version')
        obj.also_moderated = "also_moderated_value1"
        obj.save()
        return ModelWithModeratedFields.objects.all().first()

    def test_change_moderated_field_only(self):
        obj = self.get_pending_record()
        obj.moderated = "moderated_value_1"
        obj.save()

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

    def test_change_unmoderated_field_only(self):
        obj = self.get_pending_record()
        obj.unmoderated = "unmoderated_value_1"
        obj.save()

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('unmoderated_value_1', obj.unmoderated)
        self.assertEqual('unmoderated_value_1', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

    def test_change_moderated_and_unmoderated(self):
        obj = self.get_pending_record()
        obj.moderated = "moderated_value_1"
        obj.unmoderated = "unmoderated_value_1"
        obj.save()

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)

        self.assertEqual('unmoderated_value_1', obj.unmoderated)
        self.assertEqual('unmoderated_value_1', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

    def test_change_moderated_field_only_with_update_fields(self):
        obj = self.get_pending_record()
        obj.moderated = "moderated_value_1"
        obj.save(update_fields=['moderated'])

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

    def test_change_unmoderated_field_only_update_fields(self):
        obj = self.get_pending_record()
        obj.unmoderated = "unmoderated_value_1"
        obj.save(update_fields=['unmoderated'])

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('unmoderated_value_1', obj.unmoderated)
        self.assertEqual('unmoderated_value_1', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

    def test_change_moderated_and_unmoderated_update_fields(self):
        obj = self.get_pending_record()
        obj.moderated = "moderated_value_1"
        obj.unmoderated = "unmoderated_value_1"
        obj.save(update_fields=['moderated', 'unmoderated'])

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)

        self.assertEqual('unmoderated_value_1', obj.unmoderated)
        self.assertEqual('unmoderated_value_1', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

        obj.unmoderated = "unmoderated_value_2"
        obj.save(update_fields=['unmoderated'])
        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)

        self.assertEqual('unmoderated_value_2', obj.unmoderated)
        self.assertEqual('unmoderated_value_2', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

    def test_without_any_change(self):
        obj = self.get_pending_record()
        obj.save()

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value', obj.moderated_object.changed_object.moderated)

        self.assertEqual('unmoderated_value', obj.unmoderated)
        self.assertEqual('unmoderated_value', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

    def test_with_previous_values(self):
        obj = self.get_pending_record()

        # Update the moderated field
        obj.moderated = "moderated_value_1"
        obj.save()


        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

        # Update the moderated field again with the same value
        obj.moderated = "moderated_value_1"
        obj.save()
        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

        # Update the moderated with the database value
        obj.moderated = "moderated_value"
        obj.save()
        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value', obj.moderated_object.changed_object.moderated)
        #todo: should it be Pending or Approved?
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

class RejectedRecordsRegressionTestCase(TestCase):
    fixtures = ['test_users.json']

    def setUp(self):
        setup_moderation([UserProfile, ModelWithModeratedFields])
        self.user = User.objects.get(username='admin')

    def tearDown(self):
        teardown_moderation()

    def get_rejected_record(self):
        # Delete all records and create a fresh record in database
        ModelWithModeratedFields.objects.all().delete()
        obj = ModelWithModeratedFields.objects.create(moderated='moderated_value',
                                                             also_moderated='also_moderated_value',
                                                             unmoderated='unmoderated_value')
        obj.moderated_object.approve(by=self.user, reason='Initial Version')
        obj.also_moderated = "also_moderated_value1"
        obj.save()
        obj.moderated_object.reject(by=self.user, reason='Rejecting for testcase')
        return ModelWithModeratedFields.objects.all().first()

    def test_change_moderated_field_only(self):
        obj = self.get_rejected_record()
        obj.moderated = "moderated_value_1"
        obj.save()

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

    def test_change_unmoderated_field_only(self):
        obj = self.get_rejected_record()
        obj.unmoderated = "unmoderated_value_1"
        obj.save()

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('unmoderated_value_1', obj.unmoderated)
        self.assertEqual('unmoderated_value_1', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_REJECTED, obj.moderated_object.status)

    def test_change_moderated_and_unmoderated(self):
        obj = self.get_rejected_record()
        obj.moderated = "moderated_value_1"
        obj.unmoderated = "unmoderated_value_1"
        obj.save()

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)

        self.assertEqual('unmoderated_value_1', obj.unmoderated)
        self.assertEqual('unmoderated_value_1', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

    def test_change_moderated_field_only_with_update_fields(self):
        obj = self.get_rejected_record()
        obj.moderated = "moderated_value_1"
        obj.save(update_fields=['moderated'])

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

    def test_change_unmoderated_field_only_update_fields(self):
        obj = self.get_rejected_record()
        obj.unmoderated = "unmoderated_value_1"
        obj.save(update_fields=['unmoderated'])

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('unmoderated_value_1', obj.unmoderated)
        self.assertEqual('unmoderated_value_1', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_REJECTED, obj.moderated_object.status)

    def test_change_moderated_and_unmoderated_update_fields(self):
        obj = self.get_rejected_record()
        obj.moderated = "moderated_value_1"
        obj.unmoderated = "unmoderated_value_1"
        obj.save(update_fields=['moderated', 'unmoderated'])

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)

        self.assertEqual('unmoderated_value_1', obj.unmoderated)
        self.assertEqual('unmoderated_value_1', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

        obj.unmoderated = "unmoderated_value_2"
        obj.save(update_fields=['unmoderated'])
        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)

        self.assertEqual('unmoderated_value_2', obj.unmoderated)
        self.assertEqual('unmoderated_value_2', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

    def test_without_any_change(self):
        obj = self.get_rejected_record()
        obj.save()

        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value', obj.moderated_object.changed_object.moderated)

        self.assertEqual('unmoderated_value', obj.unmoderated)
        self.assertEqual('unmoderated_value', obj.moderated_object.changed_object.unmoderated)
        self.assertEqual(MODERATION_STATUS_REJECTED, obj.moderated_object.status)

    def test_with_previous_values(self):
        obj = self.get_rejected_record()

        # Update the moderated field
        obj.moderated = "moderated_value_1"
        obj.save()


        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

        # Update the moderated field again with the same value
        obj.moderated = "moderated_value_1"
        obj.save()
        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value_1', obj.moderated_object.changed_object.moderated)
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)

        # Update the moderated with the database value
        obj.moderated = "moderated_value"
        obj.save()
        obj = ModelWithModeratedFields.objects.all().first()
        self.assertEqual('moderated_value', obj.moderated)
        self.assertEqual('moderated_value', obj.moderated_object.changed_object.moderated)
        #todo: should it be Pending or Approved?
        self.assertEqual(MODERATION_STATUS_PENDING, obj.moderated_object.status)