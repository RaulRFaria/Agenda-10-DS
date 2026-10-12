
print("===== SISTEMA DE NOTAS ESCOLARES =====")

aluno = input("Nome do aluno: ")

disciplinas = ["Matemática", "Português", "História", "Ciências"]

notas = []
soma_medias = 0

for disciplina in disciplinas:
    print(f"\nDisciplina: {disciplina}")

    nota1 = float(input("Digite a primeira nota: ").replace(",", "."))
    nota2 = float(input("Digite a segunda nota: ").replace(",", "."))

    media = (nota1 + nota2) / 2

    notas.append((disciplina, media))
    soma_medias += media

    if media >= 7:
        situacao = "Aprovado"
    else:
        situacao = "Reprovado"

    print(f"Média: {media:.2f}".replace(".", ","))
    print(f"Situação: {situacao}")

media_geral = soma_medias / len(disciplinas)

print("\n===== RELATÓRIO DE DESEMPENHO =====")
print(f"Aluno: {aluno}")

for disciplina, media in notas:
    if media >= 7:
        situacao = "Aprovado"
    else:
        situacao = "Reprovado"

    media_formatada = f"{media:.2f}".replace(".", ",")
    print(f"{disciplina}: Média {media_formatada} - {situacao}")

media_geral_formatada = f"{media_geral:.2f}".replace(".", ",")

print(f"\nMédia geral: {media_geral_formatada}")

if all(media >= 7 for disciplina, media in notas):
    print("Resultado final: Aprovado em todas as disciplinas.")
else:
    print("Resultado final: Possui disciplinas com reprovação.")

print("===== Boletim Fechado" "=====")
