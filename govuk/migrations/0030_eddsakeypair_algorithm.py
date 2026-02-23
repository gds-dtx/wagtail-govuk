from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('govuk', '0029_contentdiscoverysource_send_signed_bearer_jwt'),
    ]

    operations = [
        migrations.AddField(
            model_name='eddsakeypair',
            name='algorithm',
            field=models.CharField(
                choices=[('EdDSA', 'EdDSA (Ed25519)'), ('ES256', 'ES256 (P-256)')],
                default='EdDSA',
                help_text='Signing algorithm for this key pair.',
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name='eddsakeypair',
            name='public_key',
            field=models.TextField(
                help_text='Public key in PEM format matching the selected algorithm.'
            ),
        ),
        migrations.AlterField(
            model_name='eddsakeypair',
            name='private_key',
            field=models.TextField(
                blank=True,
                help_text='Unencrypted private key in PEM format matching the selected algorithm. Stored securely and hidden after save.',
            ),
        ),
    ]
