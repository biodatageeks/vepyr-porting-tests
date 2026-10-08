use strict;
use warnings;
use JSON::PP;
use Bio::EnsEMBL::VEP::Config;
use Bio::EnsEMBL::VEP::AnnotationSource::Cache::Transcript;
my $cfg = Bio::EnsEMBL::VEP::Config->new({offline => 1, cache => 1, merged => 1, species => 'homo_sapiens', assembly => 'GRCh38', cache_version => 116});
my $c = Bio::EnsEMBL::VEP::AnnotationSource::Cache::Transcript->new({config => $cfg, dir => '/cache/homo_sapiens_merged/116_GRCh38', source_type => 'merged', cache_region_size => 1000000, valid_chromosomes => ['21']});
sub read_features {
    my $obj = $c->deserialize_from_file('/cache/homo_sapiens_merged/116_GRCh38/21/5000001-6000000.gz');
    return $c->deserialized_obj_to_features($obj);
}
sub selected {
    my $features=shift;
    return [map { +{id => $_->stable_id, symbol => $_->{_gene_symbol}, hgnc => $_->{_gene_hgnc_id}} } grep { ($_->{_gene_symbol} || '') eq 'LINC01670' } @$features];
}
my $features=read_features();
my $before=selected($features);
my $after=selected($c->merge_features($features));
my $control=read_features();
# Diagnostic only: remove donors in memory; the cache on disk is read-only.
delete $_->{_gene_hgnc_id} for grep { ($_->{_gene_symbol} || '') eq 'LINC01670' } @$control;
my $without_donors=selected($c->merge_features($control));
print JSON::PP->new->canonical->pretty->encode({before => $before, after => $after, without_donors => $without_donors});
