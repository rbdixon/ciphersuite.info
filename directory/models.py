from django.db import models
from django.utils.translation import ugettext_lazy as _
from django.db.models.signals import pre_save
from django.dispatch import receiver

from lxml import html
import requests
import re


#####################
# Model definitions #
#####################


class CipherSuite(models.Model):
    class Meta:
        ordering=['name']
        verbose_name=_('cipher suite')
        verbose_name_plural=_('cipher suites')

    # name of the cipher as defined by RFC
    name = models.CharField(
        primary_key=True,
        max_length=200,
    )
    # protocol version (SSL, TLS, etc.)
    protocol_version = models.ForeignKey(
        'ProtocolVersion',
        verbose_name=_('protocol version'),
        editable=False,
    )
    # key exchange algorithm
    kex_algorithm = models.ForeignKey(
        'KexAlgorithm',
        verbose_name=_('key exchange algorithm'),
        editable=False,
    )
    # encryption algorithm
    enc_algorithm = models.ForeignKey(
        'EncAlgorithm',
        verbose_name=_('encryption algorithm'),
        editable=False,
    )
    # hash algorithm
    hash_algorithm = models.ForeignKey(
        'HashAlgorithm',
        verbose_name=_('hash algorithm'),
        editable=False,
    )

    def __str__(self):
        return self.name


class Rfc(models.Model):
    class Meta:
        verbose_name='RFC'
        verbose_name_plural='RFCs'
        ordering=['number']

    number = models.IntegerField(
        primary_key=True,
    )

    # predefined choices for document status
    IST = 'IST'
    PST = 'PST'
    DST = 'DST'
    BCP = 'BCP'
    INF = 'INF'
    EXP = 'EXP'
    HST = 'HST'
    UND = 'UND'
    STATUS_CHOICES = (
        (IST, 'Internet Standard'),
        (PST, 'Proposed Standard'),
        (DST, 'Draft Standard'),
        (BCP, 'Best Current Practise'),
        (INF, 'Informational'),
        (EXP, 'Experimental'),
        (HST, 'Historic'),
        (UND, 'Undefined'),
    )
    status = models.CharField(
        max_length=3,
        choices=STATUS_CHOICES,
        editable=False,
    )
    title = models.CharField(
        max_length=250,
        editable=False,
    )
    release_year = models.IntegerField(
        editable=False,
    )
    url = models.URLField(
        editable=False,
    )
    defined_cipher_suites = models.ManyToManyField(
        'CipherSuite',
        verbose_name=_('defined cipher suites'),
        related_name='defining_rfcs',
        blank=True,
    )
    related_documents = models.ManyToManyField(
        'self',
        verbose_name=_('related RFCs'),
        blank=True,
    )

    def __str__(self):
        return "RFC {}".format(self.number)


class Technology(models.Model):
    class Meta:
        abstract=True
        ordering=['short_name']

    short_name = models.CharField(
        primary_key=True,
        max_length=20,
    )
    long_name = models.CharField(
        max_length=100,
    )
    vulnerabilities = models.ManyToManyField(
        'Vulnerability',
        blank=True,
    )

    def __str__(self):
        return self.short_name


class ProtocolVersion(Technology):
    class Meta(Technology.Meta):
        verbose_name=_('protocol version')
        verbose_name_plural=_('protocol versions')


class KexAlgorithm(Technology):
    class Meta(Technology.Meta):
        verbose_name=_('key exchange algorithm')
        verbose_name_plural=_('key exchange algorithms')


class EncAlgorithm(Technology):
    class Meta(Technology.Meta):
        verbose_name=_('encryption algorithm')
        verbose_name_plural=_('encryption algorithms')


class HashAlgorithm(Technology):
    class Meta(Technology.Meta):
        verbose_name=_('hash algorithm')
        verbose_name_plural=_('hash algorithms')


class Vulnerability(models.Model):
    class Meta:
        ordering=['name']
        verbose_name=_('vulnerability')
        verbose_name_plural=_('vulnerabilities')

    name = models.CharField(
        max_length=50,
    )
    description = models.TextField(
        max_length=1000,
        blank=True,
    )
    cve_id = models.CharField(
        max_length=100,
        blank=True,
    )

    def __str__(self):
        return self.name


######################
# Signal definitions #
######################


@receiver(pre_save, sender=Rfc)
def complete_rfc_instance(sender, instance, *args, **kwargs):
    """Automatically fetches general document information
    from ietf.org before saving RFC instance."""

    def get_year(response):
        tree = html.fromstring(response.content)
        docinfo = " ".join(
            tree.xpath('//pre[1]/text()')
        )
        month_list = ['January', 'February', 'March', 'April', 'May', 'June',
            'July', 'August', 'September', 'October', 'November', 'December']
        month_and_year = re.compile(
            r'\b(?:%s)\b (\d{4})' % '|'.join(month_list)
        )
        match = month_and_year.search(docinfo)
        return int(match.group(1))

    def get_title(response):
        tree = html.fromstring(response.content)
        headers = tree.xpath('//span[@class="h1"]/text()')
        return " ".join(headers)

    def get_status(response):
        tree = html.fromstring(response.content)
        # concat all fields possibly containing doc status
        docinfo = " ".join(
            tree.xpath('//span[@class="pre noprint docinfo"]/text()')
        )

        # search for predefined options
        if re.search('INTERNET STANDARD', docinfo):
            return 'IST'
        elif re.search('PROPOSED STANDARD', docinfo):
            return 'PST'
        elif re.search('DRAFT STANDARD', docinfo):
            return 'DST'
        elif re.search('BEST CURRENT PRACTISE', docinfo):
            return 'BCP'
        elif re.search('INFORMATIONAL', docinfo):
            return 'INF'
        elif re.search('EXPERIMENTAL', docinfo):
            return 'EXP'
        elif re.search('HISTORIC', docinfo):
            return 'HST'
        else:
            return 'UND'

    url = "https://tools.ietf.org/html/rfc{}".format(instance.number)
    resp = requests.get(url)
    if resp.status_code == 200:
        text = resp.text
        instance.url  = url
        instance.title = get_title(resp)
        instance.status = get_status(resp)
        instance.release_year = get_year(resp)
    else:
        # cancel saving the instance if unable to receive web page
        raise Exception('RFC not found')


@receiver(pre_save, sender=CipherSuite)
def complete_cs_instance(sender, instance, *args, **kwargs):
    # derive related algorithms form self.name
    (prt,_,rst) = instance.name.replace("_", " ").partition(" ")
    (kex,_,rst) = rst.partition("WITH")
    (enc,_,hsh) = rst.rpartition(" ")

    instance.protocol_version, _ = ProtocolVersion.objects.get_or_create(
        short_name=prt.strip()
    )
    instance.kex_algorithm, _ = KexAlgorithm.objects.get_or_create(
        short_name=kex.strip()
    )
    instance.enc_algorithm, _ = EncAlgorithm.objects.get_or_create(
        short_name=enc.strip()
    )
    instance.hash_algorithm, _ = HashAlgorithm.objects.get_or_create(
        short_name=hsh.strip()
    )
