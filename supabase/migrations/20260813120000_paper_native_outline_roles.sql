-- Papers have sections, not chapters. The initial paper support reused the
-- book outline classifier, so numbered headings such as "1 Introduction" and
-- "2 Method" were stored as chapters. Correct existing canonical node roles;
-- content, hierarchy, citations, and derived chunks remain unchanged.
update public.nodes as nodes
set node_type = case
    when nodes.toc_level = 1 then 'section'
    when nodes.toc_level = 2 then 'subsection'
    else 'nested_section'
end
from public.books as documents
where documents.id = nodes.book_id
  and documents.owner_id = nodes.owner_id
  and documents.document_type = 'paper'
  and nodes.node_type is distinct from case
      when nodes.toc_level = 1 then 'section'
      when nodes.toc_level = 2 then 'subsection'
      else 'nested_section'
  end;
